"""Duplicate detection within a search batch and against stored jobs.

* Same source + same source id/URL  → same job (rediscovered, not new)
* Different sources, same company + near-identical title (+ compatible
  location)                          → cross-posted duplicate; kept once,
                                       other copies recorded under "also on"
Distinct postings from the same source are never merged (agencies often post
several genuinely separate "Hindi Linguist" projects).
"""
from __future__ import annotations

import hashlib
import re

from rapidfuzz import fuzz

from .models import JobRecord

_CO_SUFFIX = re.compile(
    r"\b(inc|inc\.|llc|ltd|ltd\.|limited|pvt|private|gmbh|plc|corp|corporation|co|company|s\.?a\.?|b\.?v\.?|ag|technologies|technology|global|group)\b",
    re.I,
)
_TITLE_NOISE = re.compile(
    r"\b(remote|work from home|wfh|freelance|freelancer|contract|contractor|part[- ]time|full[- ]time|urgent|hiring|"
    r"immediate|india|worldwide|anywhere|apply now|job|position|opening)\b",
    re.I,
)


def norm_company(c: str | None) -> str:
    c = re.sub(r"\(.*?\)", " ", (c or "").lower())
    c = _CO_SUFFIX.sub(" ", c)
    return re.sub(r"[^a-z0-9]+", " ", c).strip()


def norm_title(t: str) -> str:
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", t.lower())
    t = _TITLE_NOISE.sub(" ", t)
    t = t.replace("&", " and ")
    return re.sub(r"[^a-z0-9ऀ-ॿ]+", " ", t).strip()


def loc_bucket(job: JobRecord) -> str:
    if job.remote_type == "remote":
        return "remote"
    return re.sub(r"[^a-z]+", "", (job.location or "").lower())[:20]


def fingerprint(job: JobRecord) -> str:
    raw = f"{norm_company(job.company)}|{norm_title(job.title)}|{loc_bucket(job)}"
    return hashlib.sha1(raw.encode()).hexdigest()[:20]


def job_id(job: JobRecord) -> str:
    key = job.source_job_id or job.url or f"{job.company}|{job.title}"
    return hashlib.sha1(f"{job.source_id}|{key}".encode()).hexdigest()[:24]


def is_same_posting(a: JobRecord, b: JobRecord) -> bool:
    if a.source_id == b.source_id:
        return (a.source_job_id or a.url) == (b.source_job_id or b.url)
    if a.url and b.url and a.url.split("?")[0] == b.url.split("?")[0]:
        return True
    ca, cb = norm_company(a.company), norm_company(b.company)
    if not ca or not cb or fuzz.ratio(ca, cb) < 88:
        return False
    if fuzz.token_sort_ratio(norm_title(a.title), norm_title(b.title)) < 92:
        return False
    la, lb = loc_bucket(a), loc_bucket(b)
    return la == lb or not la or not lb


def dedupe_batch(jobs: list[JobRecord]) -> tuple[list[JobRecord], dict[int, list[JobRecord]], int, int]:
    """Returns (unique jobs, {index_in_unique: [duplicates]}, cross_source_duplicates, repeat_hits).

    repeat_hits = the same posting returned again by overlapping queries/pages of one source.

    Uses fingerprint buckets for speed, then fuzzy comparison for near-misses
    among jobs sharing a normalized company name.
    """
    unique: list[JobRecord] = []
    dupes: dict[int, list[JobRecord]] = {}
    by_fp: dict[str, int] = {}
    by_company: dict[str, list[int]] = {}
    seen_ids: set[str] = set()
    count = 0
    repeats = 0
    for job in jobs:
        jid = job_id(job)
        if jid in seen_ids:  # exact repeat from paging / overlapping queries
            repeats += 1
            continue
        seen_ids.add(jid)
        fp = fingerprint(job)
        idx = by_fp.get(fp)
        if idx is not None and unique[idx].source_id != job.source_id:
            dupes.setdefault(idx, []).append(job)
            count += 1
            continue
        match_idx = None
        for cand in by_company.get(norm_company(job.company), []):
            if unique[cand].source_id != job.source_id and is_same_posting(unique[cand], job):
                match_idx = cand
                break
        if match_idx is not None:
            dupes.setdefault(match_idx, []).append(job)
            count += 1
            continue
        unique.append(_richer(job))
        i = len(unique) - 1
        by_fp.setdefault(fp, i)
        by_company.setdefault(norm_company(job.company), []).append(i)
    # Prefer the copy with the longer description / direct company source
    for i, ds in dupes.items():
        best = max([unique[i]] + ds, key=_quality)
        if best is not unique[i]:
            ds = [unique[i]] + [d for d in ds if d is not best]
            dupes[i] = ds
            unique[i] = best
    return unique, dupes, count, repeats


def _richer(job: JobRecord) -> JobRecord:
    return job


def _quality(job: JobRecord) -> tuple:
    direct = job.source_id in ("greenhouse", "lever", "ashby", "workable", "smartrecruiters", "careers")
    return (direct, len(job.description or ""), bool(job.salary_min), bool(job.posted_at))
