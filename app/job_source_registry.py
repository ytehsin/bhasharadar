"""Source registry: which connectors exist, their config, enable state and health."""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote_plus

from . import db
from .audit_log import log
from .connectors import CONNECTOR_CLASSES
from .connectors.base import BaseConnector

# Default company boards — each confirmed to exist via live public job pages on 2026-10-27.
DEFAULT_SOURCES: list[dict[str, Any]] = [
    {"id": "remotive", "kind": "remotive"},
    {"id": "himalayas", "kind": "himalayas"},
    {"id": "jobicy", "kind": "jobicy"},
    {"id": "remoteok", "kind": "remoteok"},
    {"id": "arbeitnow", "kind": "arbeitnow"},
    {"id": "themuse", "kind": "themuse"},
    {
        "id": "greenhouse",
        "kind": "greenhouse",
        "config": {
            "boards": [
                {"token": "agency", "company": "Meridial (Invisible) — AI trainer projects"},
                {"token": "scaleai", "company": "Scale AI"},
            ]
        },
    },
    {
        "id": "lever",
        "kind": "lever",
        "config": {
            "boards": [
                {"token": "weloglobal", "company": "Welocalize (Welo Global)"},
                {"token": "rws", "company": "RWS (TrainAI)"},
            ]
        },
    },
    {"id": "ashby", "kind": "ashby", "config": {"boards": []}},
    {"id": "workable", "kind": "workable", "config": {"boards": []}},
    {"id": "smartrecruiters", "kind": "smartrecruiters", "config": {"boards": []}},
    {"id": "adzuna", "kind": "adzuna", "config": {"countries": ["in", "gb"]}},
    {"id": "jooble", "kind": "jooble", "config": {"locations": ["India", "Remote"]}},
    {"id": "usajobs", "kind": "usajobs"},
    {
        "id": "rss",
        "kind": "rss",
        "config": {"feeds": [{"name": "We Work Remotely", "url": "https://weworkremotely.com/remote-jobs.rss"}]},
    },
    {"id": "careers", "kind": "careers", "config": {"pages": []}},
]

# Sites that do not offer a permitted automated feed: we only build search links.
MANUAL_SEARCH_SITES: list[dict[str, str]] = [
    {"name": "LinkedIn Jobs", "url": "https://www.linkedin.com/jobs/search/?keywords={q}&f_WT=2", "why": "No public API; login + anti-bot"},
    {"name": "Naukri", "url": "https://www.naukri.com/{slug}-jobs", "why": "No public API"},
    {"name": "Indeed India", "url": "https://in.indeed.com/jobs?q={q}", "why": "Publisher API closed; anti-bot"},
    {"name": "Foundit (Monster India)", "url": "https://www.foundit.in/srp/results?query={q}", "why": "No public API"},
    {"name": "ProZ.com jobs", "url": "https://www.proz.com/translation-jobs", "why": "Translator marketplace; login for details"},
    {"name": "TranslatorsCafe jobs", "url": "https://www.translatorscafe.com/cafe/SearchJobs.asp", "why": "Translator marketplace"},
    {"name": "Localization Academy job board", "url": "https://localizationacademy.com/?s={q}", "why": "Localization-specific listings"},
    {"name": "Upwork", "url": "https://www.upwork.com/nx/search/jobs/?q={q}", "why": "Login required; ToS forbids scraping"},
    {"name": "Wellfound", "url": "https://wellfound.com/jobs?query={q}", "why": "No public API"},
    {"name": "Mercor", "url": "https://work.mercor.com/", "why": "AI training/evaluation contracts (incl. Hindi)"},
    {"name": "micro1", "url": "https://jobs.micro1.ai/", "why": "AI language expert roles (incl. Hindi)"},
    {"name": "OpenTrain AI", "url": "https://www.opentrain.ai/jobs/", "why": "AI data annotation/transcription gigs"},
    {"name": "DataAnnotation", "url": "https://dataannotation.tech/", "why": "AI training work; apply via assessment"},
    {"name": "Outlier", "url": "https://outlier.ai/", "why": "AI training; account required"},
    {"name": "TELUS Digital AI Community", "url": "https://www.telusdigital.com/careers/ai-community", "why": "Search/ads/AI rater roles"},
    {"name": "CrowdGen (Appen)", "url": "https://crowdgen.com/", "why": "Language & annotation projects"},
    {"name": "OneForma (Pactera EDGE)", "url": "https://www.oneforma.com/jobs/", "why": "Transcription, annotation, linguists"},
    {"name": "Toloka", "url": "https://toloka.ai/", "why": "Annotation / AI tutor projects"},
    {"name": "Lionbridge AI / careers", "url": "https://www.lionbridge.com/join-our-team/", "why": "Language & AI data roles (Workday)"},
    {"name": "TransPerfect careers", "url": "https://careers.transperfect.com/", "why": "Translation/LQA roles"},
]


def manual_links(query: str) -> list[dict[str, str]]:
    slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")
    return [
        {"name": s["name"], "why": s["why"], "url": s["url"].format(q=quote_plus(query), slug=slug)}
        for s in MANUAL_SEARCH_SITES
    ]


def seed_sources() -> None:
    for spec in DEFAULT_SOURCES:
        cls = CONNECTOR_CLASSES[spec["kind"]]
        exists = db.query_one("SELECT id FROM sources WHERE id=?", (spec["id"],))
        if exists:
            continue
        enabled = cls.meta.default_enabled
        db.execute(
            "INSERT INTO sources(id,kind,name,enabled,config,builtin) VALUES(?,?,?,?,?,1)",
            (spec["id"], spec["kind"], cls.meta.name, int(enabled), json.dumps(spec.get("config", {}))),
        )


def list_sources() -> list[dict[str, Any]]:
    rows = db.query("SELECT * FROM sources ORDER BY builtin DESC, name")
    out = []
    for r in rows:
        cls = CONNECTOR_CLASSES.get(r["kind"])
        if not cls:
            continue
        meta = cls.meta
        r["config"] = db.jload(r["config"], {})
        conn = cls(r["id"], r["name"], r["config"])
        missing = conn.missing_keys()
        r["meta"] = {
            "source_type": meta.source_type,
            "docs_url": meta.docs_url,
            "endpoint": meta.endpoint,
            "auth": meta.auth,
            "free_tier": meta.free_tier,
            "rate_limit": meta.rate_limit,
            "coverage": meta.coverage,
            "filters": meta.filters,
            "terms": meta.terms,
            "verification": meta.verification,
            "attribution": meta.attribution,
            "required_keys": meta.required_keys,
        }
        r["missing_keys"] = missing
        if missing and r["health"] in ("unknown", "ok"):
            r["health"] = "needs_key"
        out.append(r)
    return out


def get_connector(source_id: str) -> BaseConnector | None:
    row = db.query_one("SELECT * FROM sources WHERE id=?", (source_id,))
    if not row:
        return None
    cls = CONNECTOR_CLASSES[row["kind"]]
    return cls(row["id"], row["name"], db.jload(row["config"], {}))


def enabled_connectors(only: list[str] | None = None) -> list[BaseConnector]:
    rows = db.query("SELECT * FROM sources WHERE enabled=1")
    out = []
    for row in rows:
        if only and row["id"] not in only:
            continue
        cls = CONNECTOR_CLASSES.get(row["kind"])
        if cls:
            out.append(cls(row["id"], row["name"], db.jload(row["config"], {})))
    return out


def update_source(source_id: str, *, enabled: bool | None = None, config: dict | None = None, name: str | None = None) -> None:
    row = db.query_one("SELECT * FROM sources WHERE id=?", (source_id,))
    if not row:
        raise KeyError(source_id)
    if enabled is not None:
        db.execute("UPDATE sources SET enabled=? WHERE id=?", (int(enabled), source_id))
    if config is not None:
        db.execute("UPDATE sources SET config=? WHERE id=?", (json.dumps(config), source_id))
    if name:
        db.execute("UPDATE sources SET name=? WHERE id=?", (name, source_id))
    log("sources", "update", source_id=source_id, enabled=enabled, config_changed=config is not None)


def add_custom_source(kind: str, name: str, config: dict) -> str:
    if kind not in CONNECTOR_CLASSES:
        raise ValueError(f"Unknown connector kind {kind}")
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or kind
    sid = f"custom-{base}"
    n = 1
    while db.query_one("SELECT 1 FROM sources WHERE id=?", (sid,)):
        n += 1
        sid = f"custom-{base}-{n}"
    db.execute(
        "INSERT INTO sources(id,kind,name,enabled,config,builtin) VALUES(?,?,?,1,?,0)",
        (sid, kind, name, json.dumps(config)),
    )
    log("sources", "add", source_id=sid, kind=kind)
    return sid


def delete_custom_source(source_id: str) -> None:
    db.execute("DELETE FROM sources WHERE id=? AND builtin=0", (source_id,))


def record_result(source_id: str, *, ok: bool, count: int = 0, error: str | None = None, health: str | None = None) -> None:
    now = db.now_iso()
    if ok:
        db.execute(
            "UPDATE sources SET health='ok', last_success_at=?, last_error=NULL, last_count=?, last_run_at=? WHERE id=?",
            (now, count, now, source_id),
        )
    else:
        db.execute(
            "UPDATE sources SET health=?, last_error=?, last_run_at=? WHERE id=?",
            (health or "error", (error or "")[:500], now, source_id),
        )
