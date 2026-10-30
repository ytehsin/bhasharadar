"""The GO search lifecycle.

Nothing here runs at start-up: a search only begins when the user presses GO
(or calls POST /api/search). Each enabled connector runs independently with a
concurrency limit, its own timeout and error capture, so one failing source
never stops the others. Progress is reported per source and per stage, the
session is saved, and a cancelled/crashed session can be resumed (completed
sources are served from the HTTP cache, so resuming is cheap).
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from . import config, db
from . import job_normalizer as N
from .audit_log import log
from .connectors.base import SearchContext, SkipSource
from .deduplication_engine import dedupe_batch, fingerprint, job_id
from .http_client import FetchError, HttpClient, RateLimited
from .job_source_registry import enabled_connectors, record_result
from .matching_engine import Matcher, quick_relevance
from .models import JobRecord, SearchFilters
from .profile_store import get_profile
from .query_generator import generate

STAGES = [
    "Initializing",
    "Searching sources",
    "Processing job listings",
    "Removing duplicates",
    "Matching against resume",
    "Preparing results",
    "Completed",
]
# Sources that return a company's complete current listing: a job missing from a
# successful fetch has been taken down → mark expired.
COMPLETE_LISTING_SOURCES = {"greenhouse", "lever", "ashby", "workable", "smartrecruiters", "remotive"}
SOURCE_TIMEOUT_S = 180


def _run_isolated(conn, template: HttpClient, queries, filters, cancel: asyncio.Event):
    """Run one connector to completion on a private event loop in the current (worker) thread."""

    async def inner():
        http = HttpClient(timeout=template._client.timeout.read or config.HTTP_TIMEOUT, max_retries=template.max_retries,
                          cache_ttl=template.cache_ttl, backoff_base=template.backoff_base,
                          respect_intervals=template.respect_intervals)
        conn.prepare(http)
        ctx = SearchContext(http=http, queries=queries, filters=filters, cancel=cancel)  # only .is_set() is read
        try:
            return await asyncio.wait_for(conn.search(ctx), timeout=SOURCE_TIMEOUT_S)
        finally:
            await http.aclose()

    return asyncio.run(inner())


def get_weights() -> dict[str, float]:
    from .matching_engine import DEFAULT_WEIGHTS

    return {**DEFAULT_WEIGHTS, **(db.get_setting("match_weights", {}) or {})}


class SearchRun:
    def __init__(self, filters: SearchFilters, session_id: str | None = None, http: HttpClient | None = None):
        self.id = session_id or datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        self.filters = filters
        self.cancel = asyncio.Event()
        self.stage = "Initializing"
        self.status = "running"
        self.sources: dict[str, dict[str, Any]] = {}
        self.stats: dict[str, Any] = {
            "discovered": 0, "relevant": 0, "filtered_out": 0, "duplicates_removed": 0,
            "new": 0, "rediscovered": 0, "eligible_matches": 0, "expired_marked": 0, "repeat_hits": 0,
        }
        self.queries: list[str] = []
        self.error: str | None = None
        self._http = http
        self.started_at = db.now_iso()
        self.task: asyncio.Task | None = None

    # ------------------------------------------------------------ reporting
    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "stage": self.stage,
            "stage_index": STAGES.index(self.stage) if self.stage in STAGES else 0,
            "stages": STAGES,
            "sources": self.sources,
            "stats": self.stats,
            "queries": self.queries,
            "error": self.error,
            "started_at": self.started_at,
        }

    def _persist(self, finished: bool = False) -> None:
        done = [s for s, v in self.sources.items() if v["state"] in ("done", "error", "skipped")]
        db.execute(
            "INSERT INTO search_sessions(id,started_at,finished_at,status,stage,filters,stats,progress,checkpoint) "
            "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET finished_at=excluded.finished_at, status=excluded.status, "
            "stage=excluded.stage, stats=excluded.stats, progress=excluded.progress, checkpoint=excluded.checkpoint",
            (
                self.id, self.started_at, db.now_iso() if finished else None, self.status, self.stage,
                self.filters.model_dump_json(), json.dumps(self.stats),
                json.dumps({"sources": self.sources, "queries": self.queries, "error": self.error}),
                json.dumps({"completed_sources": done}),
            ),
        )

    def _set_stage(self, stage: str) -> None:
        self.stage = stage
        self._persist()

    # ------------------------------------------------------------------ run
    async def run(self) -> None:
        http = self._http or HttpClient()
        try:
            await self._run(http)
            if self.cancel.is_set():
                self.status = "cancelled"
            else:
                self.status = "completed"
                self.stage = "Completed"
                try:  # auto-outreach only does anything if you enabled Automatic mode
                    from .email_outreach import auto_outreach_for_session

                    self.stats["outreach_queued"] = await asyncio.to_thread(auto_outreach_for_session, self.id)
                except Exception as exc:  # never fail the search because of outreach
                    self.stats["outreach_error"] = type(exc).__name__
        except asyncio.CancelledError:
            self.status = "cancelled"
        except Exception as exc:  # pragma: no cover - safety net
            self.status = "failed"
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self._persist(finished=True)
            log("search", self.status, session=self.id, stats=self.stats)
            if not self._http:
                await http.aclose()

    async def _run(self, http: HttpClient) -> None:
        self._set_stage("Initializing")
        import time as _t

        db.execute("DELETE FROM http_cache WHERE fetched_at < ?", (_t.time() - 86400,))  # keep the database small
        profile, resume_text = await asyncio.to_thread(get_profile)
        self.queries = await asyncio.to_thread(generate, profile, self.filters)
        connectors = enabled_connectors(self.filters.sources or None)
        for c in connectors:
            self.sources[c.source_id] = {"name": c.name, "state": "queued", "fetched": 0, "kept": 0, "error": None}
        self._persist()

        # ---- search
        self._set_stage("Searching sources")
        sem = asyncio.Semaphore(min(config.MAX_CONCURRENCY, 3) if config.CLOUD else config.MAX_CONCURRENCY)
        results: dict[str, list[JobRecord]] = {}
        succeeded: set[str] = set()

        async def run_one(conn) -> None:
            st = self.sources[conn.source_id]
            async with sem:
                if self.cancel.is_set():
                    st["state"] = "cancelled"
                    return
                st["state"] = "running"
                ctx = SearchContext(http=http, queries=self.queries, filters=self.filters, cancel=self.cancel)
                configured, why = conn.is_configured()
                if not configured:
                    st.update(state="skipped", error=why)
                    self._persist()
                    return
                try:
                    if conn.missing_keys():
                        raise SkipSource("Needs API key: " + ", ".join(conn.missing_keys()))
                    # Each source runs in its own thread + event loop, so its JSON parsing and
                    # HTML clean-up never freeze the web server (Render restarts frozen apps).
                    jobs = await asyncio.to_thread(_run_isolated, conn, http, self.queries, self.filters, self.cancel)
                    fetched = len(jobs)
                    # Filter now, off the event loop, so irrelevant listings are freed immediately
                    kept, dropped = await asyncio.to_thread(self._filter_batch, jobs)
                    del jobs
                    results[conn.source_id] = kept
                    self.stats["discovered"] += fetched
                    self.stats["filtered_out"] += dropped
                    st.update(state="done", fetched=fetched, kept=len(kept))
                    errs = getattr(conn, "board_errors", None)
                    if errs:
                        st["warning"] = "; ".join(f"{k}: {v}" for k, v in errs.items())[:300]
                    succeeded.add(conn.source_id)
                    record_result(conn.source_id, ok=True, count=fetched)
                except SkipSource as exc:
                    st.update(state="skipped", error=str(exc))
                    record_result(conn.source_id, ok=False, error=str(exc), health="needs_key")
                except RateLimited as exc:
                    st.update(state="error", error=str(exc))
                    record_result(conn.source_id, ok=False, error=str(exc), health="rate_limited")
                except asyncio.TimeoutError:
                    st.update(state="error", error=f"Timed out after {SOURCE_TIMEOUT_S}s")
                    record_result(conn.source_id, ok=False, error="timeout")
                except (FetchError, Exception) as exc:  # isolate every source
                    st.update(state="error", error=f"{type(exc).__name__}: {exc}"[:300])
                    record_result(conn.source_id, ok=False, error=str(exc))
                finally:
                    self._persist()

        await asyncio.gather(*(run_one(c) for c in connectors))
        if self.cancel.is_set():
            return

        # ---- process (already filtered per source above)
        self._set_stage("Processing job listings")
        all_jobs: list[JobRecord] = [j for jobs in results.values() for j in jobs]
        results.clear()
        self.stats["relevant"] = len(all_jobs)

        # ---- dedupe
        self._set_stage("Removing duplicates")
        unique, dupes, dup_count, repeats = await asyncio.to_thread(dedupe_batch, all_jobs)
        self.stats["duplicates_removed"] = dup_count
        self.stats["repeat_hits"] = repeats
        self.stats["relevant"] = len(all_jobs) - repeats

        # ---- match
        self._set_stage("Matching against resume")
        matcher = Matcher(profile, resume_text, get_weights(), self.filters,
                          use_embeddings=bool(db.get_setting("use_embeddings", False)))
        scored = await asyncio.to_thread(matcher.score_many, unique)

        # ---- store
        self._set_stage("Preparing results")
        seen_ids = await asyncio.to_thread(self._store, unique, dupes, scored)
        self.stats["expired_marked"] = await asyncio.to_thread(self._mark_expired, succeeded, seen_ids)
        self.stats["eligible_matches"] = sum(
            1 for m in scored if m["label"] != "Not Eligible" and m["score"] >= max(self.filters.min_score, 50)
        )

    def _filter_batch(self, jobs: list[JobRecord]) -> tuple[list[JobRecord], int]:
        kept = [j for j in jobs if quick_relevance(j) and self._passes_hard_filters(j)]
        return kept, len(jobs) - len(kept)

    def _passes_hard_filters(self, j: JobRecord) -> bool:
        f = self.filters
        if f.posted_within_days and j.posted_at:
            age = N.age_days(j.posted_at)
            if age is not None and age > f.posted_within_days:
                return False
        if j.deadline:
            age = N.age_days(j.deadline)
            if age is not None and age > 0:
                return False  # deadline passed
        return True

    def _store(self, unique: list[JobRecord], dupes: dict[int, list[JobRecord]], scored: list[dict]) -> set[str]:
        now = db.now_iso()
        seen: set[str] = set()
        with db.tx() as c:
            for i, (job, match) in enumerate(zip(unique, scored)):
                jid = job_id(job)
                fp = fingerprint(job)
                also = [{"source": d.source_name, "url": d.url} for d in dupes.get(i, [])]
                for d in dupes.get(i, []):
                    seen.add(job_id(d))
                existing = c.execute("SELECT id, also_on FROM jobs WHERE id=?", (jid,)).fetchone()
                if existing is None:
                    # Same posting already stored from another source?
                    other = c.execute(
                        "SELECT id, also_on, source_id FROM jobs WHERE fingerprint=? AND source_id!=?", (fp, job.source_id)
                    ).fetchone()
                    if other is not None:
                        merged = db.jload(other["also_on"], []) + [{"source": job.source_name, "url": job.url}] + also
                        merged = list({m["url"]: m for m in merged if m.get("url")}.values())
                        c.execute("UPDATE jobs SET also_on=?, last_seen_at=?, last_verified_at=?, status='active' WHERE id=?",
                                  (json.dumps(merged), now, now, other["id"]))
                        self.stats["duplicates_removed"] += 1
                        self.stats["rediscovered"] += 1
                        seen.add(other["id"])
                        seen.add(jid)
                        continue
                seen.add(jid)
                values = (
                    fp, job.source_id, job.source_name, job.source_job_id, job.title, job.company, job.location,
                    job.remote_type, json.dumps(job.eligible_regions), job.job_type, job.seniority,
                    job.salary_min, job.salary_max, job.salary_currency, job.salary_period, job.salary_text,
                    job.description[:20000], job.url, job.apply_url, job.posted_at, job.deadline,
                    json.dumps(job.tags), match["score"], match["label"], json.dumps(match),
                )
                if existing is None:
                    c.execute(
                        "INSERT INTO jobs(fingerprint,source_id,source_name,source_job_id,title,company,location,remote_type,"
                        "eligible_regions,job_type,seniority,salary_min,salary_max,salary_currency,salary_period,salary_text,"
                        "description,url,apply_url,posted_at,deadline,tags,match_score,match_label,match_detail,"
                        "id,first_seen_at,last_seen_at,last_verified_at,status,also_on,last_session_id,new_in_session) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'active',?,?,?)",
                        values + (jid, now, now, now, json.dumps(also), self.id, self.id),
                    )
                    self.stats["new"] += 1
                else:
                    merged = db.jload(existing["also_on"], []) + also
                    merged = list({m["url"]: m for m in merged if m.get("url")}.values())
                    c.execute(
                        "UPDATE jobs SET fingerprint=?,source_id=?,source_name=?,source_job_id=?,title=?,company=?,location=?,"
                        "remote_type=?,eligible_regions=?,job_type=?,seniority=?,salary_min=?,salary_max=?,salary_currency=?,"
                        "salary_period=?,salary_text=?,description=?,url=?,apply_url=?,posted_at=?,deadline=?,tags=?,"
                        "match_score=?,match_label=?,match_detail=?,last_seen_at=?,last_verified_at=?,status='active',"
                        "also_on=?,last_session_id=? WHERE id=?",
                        values + (now, now, json.dumps(merged), self.id, jid),
                    )
                    self.stats["rediscovered"] += 1
        return seen

    def _mark_expired(self, succeeded: set[str], seen_ids: set[str]) -> int:
        n = 0
        kinds = {r["id"]: r["kind"] for r in db.query("SELECT id, kind FROM sources")}
        for sid in succeeded:
            if kinds.get(sid) not in COMPLETE_LISTING_SOURCES or self.sources.get(sid, {}).get("warning"):
                continue  # partial fetch (a board failed) → can't infer removals
            rows = db.query("SELECT id FROM jobs WHERE source_id=? AND status='active'", (sid,))
            for r in rows:
                if r["id"] not in seen_ids:
                    db.execute("UPDATE jobs SET status='expired', last_verified_at=? WHERE id=?", (db.now_iso(), r["id"]))
                    n += 1
        # Deadlines in the past
        today = datetime.now(timezone.utc).isoformat()
        n += db.execute("UPDATE jobs SET status='expired' WHERE status='active' AND deadline IS NOT NULL AND deadline < ?", (today,)) or 0
        return n


class SearchManager:
    def __init__(self) -> None:
        self.runs: dict[str, SearchRun] = {}
        self.current: SearchRun | None = None

    def start(self, filters: SearchFilters, resume_id: str | None = None, http: HttpClient | None = None) -> SearchRun:
        if self.current and self.current.status == "running":
            raise RuntimeError("A search is already running")
        if resume_id:
            row = db.query_one("SELECT filters FROM search_sessions WHERE id=?", (resume_id,))
            if row:
                filters = SearchFilters(**json.loads(row["filters"]))
        run = SearchRun(filters, http=http)
        self.runs[run.id] = run
        self.current = run
        run.task = asyncio.create_task(run.run())
        log("search", "started", session=run.id, resumed_from=resume_id)
        return run

    def cancel(self, run_id: str | None = None) -> bool:
        run = self.runs.get(run_id) if run_id else self.current
        if not run or run.status != "running":
            return False
        run.cancel.set()
        return True

    def status(self, run_id: str | None = None) -> dict[str, Any] | None:
        run = self.runs.get(run_id) if run_id else self.current
        if run:
            return run.snapshot()
        row = db.query_one("SELECT * FROM search_sessions WHERE id=?", (run_id,)) if run_id else None
        if not row:
            return None
        prog = db.jload(row["progress"], {})
        return {"id": row["id"], "status": row["status"], "stage": row["stage"], "stages": STAGES,
                "stage_index": STAGES.index(row["stage"]) if row["stage"] in STAGES else 0,
                "sources": prog.get("sources", {}), "stats": db.jload(row["stats"], {}),
                "queries": prog.get("queries", []), "error": prog.get("error"), "started_at": row["started_at"]}


def mark_interrupted_sessions() -> None:
    """On startup, sessions left 'running' by a crash become resumable."""
    db.execute("UPDATE search_sessions SET status='interrupted' WHERE status='running'")


manager = SearchManager()
