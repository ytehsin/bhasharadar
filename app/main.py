"""FastAPI application — REST API + the single-page dashboard."""
from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import Body, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import application_manager as AM
from . import auth
from . import config, db, email_outreach, form_assist, gmail_oauth, job_tracker, scheduler, settings_store
from . import job_normalizer as N
from . import taxonomy as T
from .audit_log import log, recent
from .http_client import HttpClient
from .job_source_registry import (
    add_custom_source, delete_custom_source, get_connector, list_sources, manual_links, seed_sources, update_source,
)
from .matching_engine import COMPONENT_LABELS, DEFAULT_WEIGHTS, Matcher
from .models import MATCH_LABELS, TRACKER_STATUSES, JobRecord, Profile, SearchFilters
from .profile_store import delete_profile, get_profile, profile_status, save_profile
from .resume_parser import ResumeParseError, parse_resume
from .search_engine import get_weights, manager, mark_interrupted_sessions
from .security import UnsafeURLError, validate_public_url

STATIC_DIR = config.BASE_DIR / "app" / "static"
_bg_stop = asyncio.Event()


def init_storage() -> None:
    if config.CLOUD and not db.is_postgres():
        print("WARNING: no DATABASE_URL set - data will be lost whenever the server restarts.", flush=True)
    db.migrate()
    seed_sources()
    mark_interrupted_sessions()
    from .matching_engine import MATCHER_VERSION

    if db.get_setting("matcher_version") != MATCHER_VERSION:
        if db.query_one("SELECT 1 FROM jobs LIMIT 1"):
            try:
                rescore()
            except Exception as exc:  # never block start-up
                log("app", "rescore_failed", error=type(exc).__name__)
        db.set_setting("matcher_version", MATCHER_VERSION)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_storage()
    _bg_stop.clear()
    # Background loops are passive: outreach only sends what YOUR rules queued,
    # and the scheduler only runs if you enabled it. No search starts on boot.
    tasks = [asyncio.create_task(email_outreach.outreach_loop(_bg_stop)), asyncio.create_task(scheduler.scheduler_loop(_bg_stop))]
    log("app", "started", version=config.APP_VERSION)
    yield
    _bg_stop.set()
    for t in tasks:
        t.cancel()


app = FastAPI(title=config.APP_NAME, version=config.APP_VERSION, lifespan=lifespan,
              docs_url=None if config.CLOUD else "/docs", redoc_url=None, openapi_url=None if config.CLOUD else "/openapi.json")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.middleware("http")(auth.middleware)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"ok": "yes"}


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if not auth.enabled() or auth.valid(request.cookies.get(auth.COOKIE)):
        return RedirectResponse("/", status_code=303)
    return auth.login_page()


@app.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    ip = auth._client_ip(request)
    if auth.too_many_attempts(ip):
        return auth.login_page("Too many tries. Wait 15 minutes and try again.")
    if not auth.check_password(ip, str(form.get("password", ""))):
        log("auth", "login_failed")
        return auth.login_page("That password didn't match.")
    resp = RedirectResponse("/", status_code=303)
    auth.set_cookie(resp, request)
    log("auth", "login")
    return resp


@app.post("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


def bad(msg: str, code: int = 400) -> HTTPException:
    return HTTPException(status_code=code, detail=msg)


def _asset_version() -> str:
    """Changes whenever the CSS/JS change, so browsers fetch the new files after an update."""
    return str(int(max((STATIC_DIR / f).stat().st_mtime for f in ("styles.css", "app.js"))))


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    v = _asset_version()
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace('/static/styles.css"', f'/static/styles.css?v={v}"').replace('/static/app.js"', f'/static/app.js?v={v}"')
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


# ------------------------------------------------------------------ status ----
@app.get("/api/status")
def status() -> dict[str, Any]:
    srcs = list_sources()
    enabled = [s for s in srcs if s["enabled"]]
    o = email_outreach.get_settings()
    return {
        "app": config.APP_NAME,
        "version": config.APP_VERSION,
        "profile": profile_status(),
        "sources": {
            "enabled": len(enabled),
            "ok": sum(1 for s in enabled if s["health"] == "ok"),
            "error": sum(1 for s in enabled if s["health"] in ("error", "rate_limited")),
            "needs_key": sum(1 for s in enabled if s["health"] == "needs_key"),
            "unknown": sum(1 for s in enabled if s["health"] == "unknown"),
        },
        "outreach": {"mode": o["mode"], "stopped": o["stopped"], "account": o["account_configured"]},
        "search": manager.status(),
        "jobs": (db.query_one("SELECT COUNT(*) n FROM jobs WHERE hidden=0 AND status='active'") or {"n": 0})["n"],
        "auth": auth.enabled(),
        "cloud": config.CLOUD,
    }


@app.get("/api/meta")
def meta() -> dict[str, Any]:
    p, _ = get_profile()
    return {
        "categories": list(T.CATEGORY_QUERIES),
        "default_categories": p.target_roles,
        "ai_categories": sorted(T.AI_CATEGORIES),
        "localization_categories": sorted(T.LOCALIZATION_CATEGORIES),
        "statuses": TRACKER_STATUSES,
        "labels": MATCH_LABELS,
        "job_types": ["full_time", "part_time", "contract", "freelance", "internship", "temporary"],
        "locations": ["Remote (Worldwide)", "India", "Delhi NCR", "Mumbai", "Bengaluru", "Hyderabad", "Pune", "Chennai", "Anywhere"],
        "work_modes": ["remote", "hybrid", "onsite"],
        "experience_levels": ["any", "entry", "mid", "senior", "lead"],
        "weights": get_weights(),
        "default_weights": DEFAULT_WEIGHTS,
        "component_labels": COMPONENT_LABELS,
    }


# ----------------------------------------------------------------- profile ----
@app.get("/api/profile")
def get_profile_api() -> dict[str, Any]:
    p, _ = get_profile()
    return {"profile": p.model_dump(), "status": profile_status()}


@app.put("/api/profile")
def put_profile(data: dict = Body(...)) -> dict[str, Any]:
    cur, _ = get_profile()
    merged = {**cur.model_dump(), **{k: v for k, v in data.items() if k in Profile.model_fields and k != "verified"}}
    p = Profile(**merged)
    st = profile_status()
    save_profile(p, source=st["source"] if st["source"] == "resume" else "seed")
    return {"profile": p.model_dump(), "status": profile_status()}


@app.post("/api/resume")
async def upload_resume(file: UploadFile = File(...)) -> dict[str, Any]:
    data = await file.read()
    name = file.filename or "resume"
    try:
        profile, text = parse_resume(name, data)
    except ResumeParseError as exc:
        raise bad(str(exc), 422)
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else "txt"
    for old in config.UPLOAD_DIR.glob("resume.*"):
        old.unlink()
    (config.UPLOAD_DIR / f"resume.{ext}").write_bytes(data)
    db.put_file("resume", name, data)  # survives restarts on cloud hosts
    save_profile(profile, source="resume", filename=name, resume_text=text)
    return {"profile": profile.model_dump(), "status": profile_status(), "chars": len(text)}


@app.delete("/api/resume")
def delete_resume() -> dict[str, str]:
    delete_profile()
    db.delete_file("resume")
    for f in config.UPLOAD_DIR.glob("resume.*"):
        f.unlink()
    return {"ok": "Resume and parsed profile deleted. The provisional profile is used until you upload again."}


@app.delete("/api/data")
def delete_all_history(include_tracker: bool = False) -> dict[str, str]:
    db.execute("DELETE FROM jobs WHERE id NOT IN (SELECT job_id FROM applications)" if not include_tracker else "DELETE FROM jobs")
    db.execute("DELETE FROM search_sessions")
    db.execute("DELETE FROM http_cache")
    if include_tracker:
        db.execute("DELETE FROM applications")
        db.execute("DELETE FROM outreach")
    log("privacy", "history_deleted", include_tracker=include_tracker)
    return {"ok": "Search history deleted"}


# ------------------------------------------------------------------ search ----
@app.post("/api/search")
async def go(filters: SearchFilters = Body(default_factory=SearchFilters)) -> dict[str, Any]:
    try:
        db.set_setting("last_filters", filters.model_dump())
        run = manager.start(filters)
    except RuntimeError as exc:
        raise bad(str(exc), 409)
    return run.snapshot()


@app.post("/api/command")
async def command(payload: dict = Body(...)) -> dict[str, Any]:
    cmd = str(payload.get("command", "")).strip().upper()
    if cmd == "GO":
        filters = SearchFilters(**(payload.get("filters") or db.get_setting("last_filters", {}) or {}))
        return await go(filters)
    if cmd == "STOP":
        return stop_everything()
    raise bad("Unknown command. Use GO or STOP.")


@app.get("/api/search/status")
def search_status(id: Optional[str] = None) -> dict[str, Any]:
    st = manager.status(id)
    if not st:
        raise bad("No search yet", 404)
    return st


@app.post("/api/search/cancel")
def search_cancel(id: Optional[str] = None) -> dict[str, bool]:
    return {"cancelled": manager.cancel(id)}


@app.get("/api/search/sessions")
def sessions() -> list[dict[str, Any]]:
    rows = db.query("SELECT id, started_at, finished_at, status, stage, stats FROM search_sessions ORDER BY started_at DESC LIMIT 30")
    for r in rows:
        r["stats"] = db.jload(r["stats"], {})
    return rows


@app.post("/api/search/resume/{session_id}")
async def resume_session(session_id: str) -> dict[str, Any]:
    try:
        run = manager.start(SearchFilters(), resume_id=session_id)
    except RuntimeError as exc:
        raise bad(str(exc), 409)
    return run.snapshot()


@app.post("/api/stop")
def stop_everything() -> dict[str, Any]:
    cancelled_search = manager.cancel()
    n = email_outreach.emergency_stop("STOP pressed")
    return {"search_cancelled": cancelled_search, "outreach_cancelled": n, "outreach_stopped": True}


# -------------------------------------------------------------------- jobs ----
def _job_row(r: dict[str, Any], latest_session: str | None, apps: dict[str, dict]) -> dict[str, Any]:
    m = db.jload(r["match_detail"], {}) or {}
    app_row = apps.get(r["id"])
    return {
        "id": r["id"], "title": r["title"], "company": r["company"], "location": r["location"],
        "remote_type": r["remote_type"], "job_type": r["job_type"], "seniority": r["seniority"],
        "score": r["match_score"], "label": r["match_label"], "salary": salary_display(r),
        "posted_at": r["posted_at"], "deadline": r["deadline"], "source": r["source_name"], "source_id": r["source_id"],
        "url": r["url"], "apply_url": r["apply_url"] or r["url"], "saved": bool(r["saved"]), "status": r["status"],
        "is_new": r["new_in_session"] == latest_session and latest_session is not None,
        "first_seen_at": r["first_seen_at"], "last_verified_at": r["last_verified_at"],
        "also_on": db.jload(r["also_on"], []), "upskilling": m.get("upskilling"), "categories": m.get("categories", []),
        "hindi": m.get("hindi"), "application_status": app_row["status_display"] if app_row else None,
        "eligible_regions": db.jload(r["eligible_regions"], []), "effort": m.get("effort"),
        "direct": _is_direct(r), "caution": m.get("caution") or [],
    }


DIRECT_HOSTS = re.compile(r"(greenhouse\.io|lever\.co|ashbyhq\.com|workable\.com|smartrecruiters\.com|teamtailor|careers\.)", re.I)


def _is_direct(r: dict[str, Any]) -> bool:
    """Applying goes straight to the employer's own hiring system (not an aggregator)."""
    if r["source_id"] in ("greenhouse", "lever", "ashby", "workable", "smartrecruiters", "careers") or r["source_id"].startswith("custom-"):
        return True
    return bool(DIRECT_HOSTS.search(r.get("apply_url") or ""))


def salary_display(r: dict[str, Any]) -> str | None:
    if r.get("salary_min") or r.get("salary_max"):
        cur = r.get("salary_currency") or ""
        lo, hi = r.get("salary_min"), r.get("salary_max")
        fmt = lambda v: f"{v:,.0f}" if v and v >= 100 else (f"{v:g}" if v else "")
        rng = f"{fmt(lo)}–{fmt(hi)}" if lo and hi and lo != hi else fmt(lo or hi)
        per = f"/{r['salary_period']}" if r.get("salary_period") else ""
        return f"{cur} {rng}{per}".strip()
    return r.get("salary_text")


@app.get("/api/jobs")
def jobs(
    q: str = "",
    categories: str = "",
    job_types: str = "",
    work_modes: str = "",
    locations: str = "",
    experience_level: str = "any",
    min_salary: float = 0,
    min_score: float = 0,
    posted_within_days: int = 0,
    quick: str = "",
    include_ineligible: bool = False,
    include_expired: bool = False,
    saved_only: bool = False,
    session_only: bool = False,
    sort: str = "score",
    page: int = 1,
    page_size: int = Query(25, le=100),
) -> dict[str, Any]:
    latest = (db.query_one("SELECT id FROM search_sessions WHERE status='completed' ORDER BY started_at DESC LIMIT 1") or {}).get("id")
    rows = db.query("SELECT * FROM jobs WHERE hidden=0")
    apps = {a["job_id"]: a for a in job_tracker.list_applications()}
    cats = {c for c in categories.split("|") if c}
    jts = {c for c in job_types.split(",") if c}
    wms = {c for c in work_modes.split(",") if c}
    locs = {c for c in locations.split("|") if c}
    qf = {c for c in quick.split(",") if c}
    ql = q.lower().strip()
    out, hidden_ineligible, hidden_expired = [], 0, 0
    for r in rows:
        row = _job_row(r, latest, apps)
        if r["status"] == "expired" and not include_expired:
            hidden_expired += 1
            continue
        if saved_only and not row["saved"] and not row["application_status"]:
            continue
        if session_only and r["last_session_id"] != latest:
            continue
        if ql and ql not in f"{r['title']} {r['company']} {r['description'][:3000]}".lower():
            continue
        if cats and not cats & set(row["categories"]):
            continue
        if jts and r["job_type"] not in jts:
            continue
        if wms and r["remote_type"] not in wms and r["remote_type"] != "unknown":
            continue
        if locs and not _loc_ok(row, locs):
            continue
        if experience_level != "any" and (r["seniority"] or "mid") != experience_level:
            continue
        annual = N.annual_usd(r["salary_max"] or r["salary_min"], r["salary_currency"], r["salary_period"])
        if min_salary and (annual is None or annual < min_salary):
            continue
        if (r["match_score"] or 0) < min_score:
            continue
        age = N.age_days(r["posted_at"])
        if posted_within_days and age is not None and age > posted_within_days:
            continue
        if not _quick_ok(row, r, qf, annual):
            continue
        if r["match_label"] == "Not Eligible" and not include_ineligible:
            hidden_ineligible += 1
            continue
        out.append(row)
    key = {
        "score": lambda x: -(x["score"] or 0),
        "newest": lambda x: x["posted_at"] or x["first_seen_at"] or "",
        "salary": lambda x: -(N.annual_usd(*_sal(x["id"])) or 0),
        "deadline": lambda x: x["deadline"] or "9999",
    }.get(sort, lambda x: -(x["score"] or 0))
    out.sort(key=key, reverse=(sort == "newest"))
    total = len(out)
    start = (max(page, 1) - 1) * page_size
    return {"total": total, "page": page, "page_size": page_size, "items": out[start:start + page_size],
            "hidden_ineligible": hidden_ineligible, "hidden_expired": hidden_expired, "latest_session": latest}


def _sal(job_id: str) -> tuple:
    r = db.query_one("SELECT salary_min, salary_max, salary_currency, salary_period FROM jobs WHERE id=?", (job_id,)) or {}
    return (r.get("salary_max") or r.get("salary_min"), r.get("salary_currency"), r.get("salary_period"))


def _loc_ok(row: dict[str, Any], locs: set[str]) -> bool:
    if "Anywhere" in locs:
        return True
    regions = set(row["eligible_regions"])
    loc = (row["location"] or "").lower()
    for l in locs:
        if l == "Remote (Worldwide)" and row["remote_type"] == "remote" and (not regions or regions & {"Worldwide", "India"}):
            return True
        if l == "India" and ("India" in regions or "india" in loc):
            return True
        if l == "Delhi NCR" and any(x in loc for x in ("delhi", "noida", "gurgaon", "gurugram", "ghaziabad", "ncr", "faridabad")):
            return True
        if l.lower() in loc:
            return True
    return False


def _quick_ok(row: dict[str, Any], r: dict[str, Any], qf: set[str], annual: float | None) -> bool:
    if "best" in qf and (row["score"] or 0) < 65:
        return False
    if "remote" in qf and row["remote_type"] != "remote":
        return False
    if "freelance" in qf and row["job_type"] not in ("freelance", "contract", "part_time", "temporary"):
        return False
    if "recent" in qf:
        age = N.age_days(row["posted_at"] or row["first_seen_at"])
        if age is None or age > 7:
            return False
    if "no_upskill" in qf and row["upskilling"] != "None":
        return False
    if "ai" in qf and not set(row["categories"]) & T.AI_CATEGORIES:
        return False
    if "localization" in qf and not (set(row["categories"]) & T.LOCALIZATION_CATEGORIES and row["hindi"]):
        return False
    if "salary" in qf and not row["salary"]:
        return False
    if "direct" in qf and not row["direct"]:
        return False
    return True


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: str) -> dict[str, Any]:
    r = db.query_one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not r:
        raise bad("Job not found", 404)
    apps = {a["job_id"]: a for a in job_tracker.list_applications()}
    row = _job_row(r, None, apps)
    row["description"] = r["description"]
    row["match"] = db.jload(r["match_detail"], {})
    row["tags"] = db.jload(r["tags"], [])
    row["application"] = job_tracker.get_for_job(job_id)
    row["contacts"] = AM.public_contacts(r)
    src = get_connector(r["source_id"])
    row["attribution"] = src.meta.attribution if src else r["source_name"]
    row["outreach"] = db.query("SELECT id, status, recipient, template, sent_at FROM outreach WHERE job_id=? ORDER BY id DESC", (job_id,))
    return row


@app.post("/api/jobs/{job_id}/save")
def save_job(job_id: str, saved: bool = True) -> dict[str, bool]:
    db.execute("UPDATE jobs SET saved=? WHERE id=?", (int(saved), job_id))
    if saved and not job_tracker.get_for_job(job_id):
        job_tracker.set_status(job_id, "Shortlisted")
    return {"saved": saved}


@app.post("/api/jobs/{job_id}/hide")
def hide_job(job_id: str, hidden: bool = True) -> dict[str, bool]:
    db.execute("UPDATE jobs SET hidden=? WHERE id=?", (int(hidden), job_id))
    return {"hidden": hidden}


@app.post("/api/jobs/{job_id}/verify")
async def verify_job(job_id: str) -> dict[str, Any]:
    r = db.query_one("SELECT url, apply_url FROM jobs WHERE id=?", (job_id,))
    if not r:
        raise bad("Job not found", 404)
    url = r["url"] or r["apply_url"]
    try:
        validate_public_url(url)
    except UnsafeURLError as exc:
        raise bad(f"Cannot verify: {exc}")
    http = HttpClient()
    try:
        code = await http.head_status(url)
    finally:
        await http.aclose()
    status = "expired" if code in (404, 410) else "active" if code and code < 400 else "unknown"
    db.execute("UPDATE jobs SET status=?, last_verified_at=? WHERE id=?", (status if status != "unknown" else "active", db.now_iso(), job_id))
    return {"http_status": code, "status": status}


@app.post("/api/jobs/rescore")
def rescore() -> dict[str, int]:
    p, text = get_profile()
    filters = SearchFilters(**(db.get_setting("last_filters", {}) or {}))
    m = Matcher(p, text, get_weights(), filters, use_embeddings=bool(db.get_setting("use_embeddings", False)))
    rows = db.query("SELECT * FROM jobs")
    recs = [JobRecord(
        source_id=r["source_id"], source_name=r["source_name"], source_job_id=r["source_job_id"], title=r["title"],
        company=r["company"], location=r["location"], remote_type=r["remote_type"] or "unknown",
        eligible_regions=db.jload(r["eligible_regions"], []), job_type=r["job_type"] or "unknown", seniority=r["seniority"],
        salary_min=r["salary_min"], salary_max=r["salary_max"], salary_currency=r["salary_currency"],
        salary_period=r["salary_period"], salary_text=r["salary_text"], description=r["description"] or "",
        url=r["url"], apply_url=r["apply_url"], posted_at=r["posted_at"], deadline=r["deadline"], tags=db.jload(r["tags"], []),
    ) for r in rows]
    scored = m.score_many(recs)
    with db.tx() as c:
        for r, s in zip(rows, scored):
            c.execute("UPDATE jobs SET match_score=?, match_label=?, match_detail=? WHERE id=?",
                      (s["score"], s["label"], json.dumps(s), r["id"]))
    return {"rescored": len(rows)}


# ------------------------------------------------------------ applications ----
@app.get("/api/applications")
def applications(status: Optional[str] = None) -> list[dict[str, Any]]:
    return job_tracker.list_applications(status)


@app.post("/api/jobs/{job_id}/status")
def set_status(job_id: str, payload: dict = Body(...)) -> dict[str, Any]:
    try:
        return job_tracker.set_status(
            job_id, payload.get("status", ""), notes=payload.get("notes"), method=payload.get("method"),
            confirmation=payload.get("confirmation"), reapply=bool(payload.get("reapply")),
        )
    except job_tracker.DuplicateApplicationError as exc:
        raise bad(str(exc), 409)
    except (ValueError, KeyError) as exc:
        raise bad(str(exc))


@app.delete("/api/applications/{app_id}")
def delete_app(app_id: int) -> dict[str, bool]:
    job_tracker.delete_application(app_id)
    return {"deleted": True}


@app.get("/api/export/applications.csv")
def export_csv() -> Response:
    return Response(job_tracker.export_csv(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=applications.csv"})


@app.get("/api/export/applications.xlsx")
def export_xlsx() -> Response:
    return Response(job_tracker.export_xlsx(),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=applications.xlsx"})


# --------------------------------------------------------------- documents ----
def _job_or_404(job_id: str) -> dict[str, Any]:
    r = db.query_one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not r:
        raise bad("Job not found", 404)
    return r


@app.get("/api/jobs/{job_id}/cover-letter")
def get_cover_letter(job_id: str) -> dict[str, Any]:
    p, _ = get_profile()
    return {"text": AM.cover_letter(_job_or_404(job_id), p), "provisional": not p.verified}


@app.get("/api/jobs/{job_id}/form-answers")
def get_form_answers(job_id: str) -> dict[str, Any]:
    p, _ = get_profile()
    return AM.form_answers(_job_or_404(job_id), p)


@app.get("/api/jobs/{job_id}/tailored-resume.docx")
def get_tailored_resume(job_id: str) -> Response:
    p, text = get_profile()
    try:
        data = AM.tailored_resume_docx(_job_or_404(job_id), p, text)
    except ValueError as exc:
        raise bad(str(exc), 422)
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": "attachment; filename=resume-tailored.docx"})


@app.get("/api/answers")
def get_answers() -> dict[str, Any]:
    p, _ = get_profile()
    return {"fields": [{"key": k, "label": l, "placeholder": ph} for k, l, ph in AM.ANSWER_FIELDS],
            "values": AM.saved_answers(p)}


@app.put("/api/answers")
def put_answers(payload: dict = Body(...)) -> dict[str, Any]:
    p, _ = get_profile()
    allowed = {k for k, _, _ in AM.ANSWER_FIELDS}
    answers = {k: str(v).strip()[:300] for k, v in payload.items() if k in allowed}
    p.preferences = {**(p.preferences or {}), "answers": answers}
    st = profile_status()
    save_profile(p, source="resume" if st["source"] == "resume" else "seed")
    return get_answers()


@app.get("/api/resume/file")
def resume_file() -> Response:
    stored = db.get_file("resume")
    if not stored:
        raise bad("No resume uploaded yet", 404)
    name, data = stored
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else "pdf"
    media = {"pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}.get(ext, "text/plain")
    p, _ = get_profile()
    nice = re.sub(r"[^A-Za-z0-9]+", "_", p.name or "Resume").strip("_") or "Resume"
    return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{nice}_Resume.{ext}"'})


@app.get("/api/jobs/{job_id}/apply-kit")
async def get_apply_kit(job_id: str) -> dict[str, Any]:
    job = _job_or_404(job_id)
    p, _ = get_profile()
    questions = None
    ref = AM.greenhouse_ref(job)
    if ref:
        token, gid, eu = ref
        host = "boards-api.eu.greenhouse.io" if eu else "boards-api.greenhouse.io"
        http = HttpClient(max_retries=1, cache_ttl=86400)
        try:
            data = await http.get_json(f"https://{host}/v1/boards/{token}/jobs/{gid}", params={"questions": "true"})
            questions = (data.get("questions") or []) + (data.get("location_questions") or [])
        except Exception:
            questions = None  # the kit still works without the form's questions
        finally:
            await http.aclose()
    kit = AM.apply_kit(job, p, questions)
    kit["application"] = job_tracker.get_for_job(job_id)
    return kit


@app.post("/api/jobs/{job_id}/form-assist")
async def run_form_assist(job_id: str) -> dict[str, Any]:
    r = _job_or_404(job_id)
    try:
        return await form_assist.open_and_prefill(r["apply_url"] or r["url"])
    except (PermissionError, RuntimeError, UnsafeURLError) as exc:
        raise bad(str(exc), 422)


# ---------------------------------------------------------------- outreach ----
@app.get("/api/outreach/settings")
def outreach_settings() -> dict[str, Any]:
    return email_outreach.get_settings()


@app.put("/api/outreach/settings")
def put_outreach_settings(payload: dict = Body(...)) -> dict[str, Any]:
    try:
        return email_outreach.save_settings(payload)
    except email_outreach.OutreachError as exc:
        raise bad(str(exc), 422)


@app.get("/api/outreach/messages")
def outreach_messages() -> list[dict[str, Any]]:
    return email_outreach.list_messages()


@app.post("/api/outreach/draft")
def outreach_draft(payload: dict = Body(...)) -> dict[str, Any]:
    try:
        return email_outreach.create_draft(payload["job_id"], payload.get("template", "inquiry"), payload.get("recipient"))
    except (email_outreach.OutreachError, KeyError) as exc:
        raise bad(str(exc), 422)


@app.put("/api/outreach/messages/{mid}")
def outreach_update(mid: int, payload: dict = Body(...)) -> dict[str, Any]:
    try:
        return email_outreach.update_draft(mid, payload.get("subject"), payload.get("body"), payload.get("recipient"))
    except email_outreach.OutreachError as exc:
        raise bad(str(exc), 422)


@app.post("/api/outreach/messages/{mid}/approve")
def outreach_approve(mid: int) -> dict[str, Any]:
    try:
        return email_outreach.approve(mid)
    except email_outreach.OutreachError as exc:
        raise bad(str(exc), 422)


@app.post("/api/outreach/messages/{mid}/cancel")
def outreach_cancel(mid: int) -> dict[str, bool]:
    email_outreach.cancel(mid)
    return {"cancelled": True}


@app.post("/api/outreach/process")
async def outreach_process() -> dict[str, int]:
    return await email_outreach.process_queue()


@app.post("/api/outreach/stop")
def outreach_stop() -> dict[str, int]:
    return {"cancelled": email_outreach.emergency_stop("STOP pressed")}


@app.post("/api/outreach/resume")
def outreach_resume() -> dict[str, bool]:
    email_outreach.resume_sending()
    return {"stopped": False}


@app.post("/api/outreach/optout")
def outreach_optout(payload: dict = Body(...)) -> dict[str, bool]:
    email_outreach.opt_out(payload["email"], payload.get("reason", "Recipient opted out"))
    return {"ok": True}


@app.get("/api/outreach/suppression")
def suppression() -> list[dict[str, Any]]:
    return db.query("SELECT * FROM suppression ORDER BY created_at DESC")


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


@app.get("/api/email/oauth/google/start")
def gmail_start(request: Request) -> RedirectResponse:
    try:
        return RedirectResponse(gmail_oauth.start_url(_base_url(request)))
    except RuntimeError as exc:
        raise bad(str(exc), 422)


@app.get("/api/email/oauth/google/callback", response_class=HTMLResponse)
async def gmail_callback(code: str = "", state: str = "", error: str = "") -> str:
    if error:
        return f"<p>Google sign-in was cancelled ({error}). You can close this tab.</p>"
    try:
        email = await gmail_oauth.handle_callback(code, state)
    except RuntimeError as exc:
        return f"<p>Could not connect Gmail: {exc}</p>"
    return f"<p>Gmail connected{(' as ' + email) if email else ''}. You can close this tab and return to {config.APP_NAME}.</p>"


@app.post("/api/email/oauth/google/disconnect")
def gmail_disconnect() -> dict[str, bool]:
    gmail_oauth.disconnect()
    return {"ok": True}


@app.post("/api/email/test")
async def email_test() -> dict[str, Any]:
    s = email_outreach.get_settings()
    if not s["account_configured"]:
        raise bad("No email account configured", 422)
    msg = {"recipient": s["from_email"], "subject": f"{config.APP_NAME} test email", "body": "Your email connection works.",
           "attach_resume": 0}
    em = email_outreach._build_email(msg, s["from_email"], False)
    try:
        if s["provider"] == "gmail":
            await email_outreach._send_gmail(em)
        else:
            await asyncio.to_thread(email_outreach._send_smtp, em)
    except Exception as exc:
        raise bad(f"Test failed: {type(exc).__name__}: {str(exc)[:120]}", 502)
    log("outreach", "test_email_sent")
    return {"ok": True, "sent_to": s["from_email"]}


# ----------------------------------------------------------------- sources ----
@app.get("/api/sources")
def sources() -> list[dict[str, Any]]:
    return list_sources()


@app.put("/api/sources/{source_id}")
def put_source(source_id: str, payload: dict = Body(...)) -> dict[str, bool]:
    cfg = payload.get("config")
    if cfg is not None:
        _validate_source_config(cfg)
    try:
        update_source(source_id, enabled=payload.get("enabled"), config=cfg, name=payload.get("name"))
    except KeyError:
        raise bad("Unknown source", 404)
    return {"ok": True}


def _validate_source_config(cfg: dict[str, Any]) -> None:
    for f in cfg.get("feeds", []) + cfg.get("pages", []):
        url = f["url"] if isinstance(f, dict) else f
        try:
            validate_public_url(url)
        except UnsafeURLError as exc:
            raise bad(f"{url}: {exc}", 422)


@app.post("/api/sources")
def post_source(payload: dict = Body(...)) -> dict[str, str]:
    cfg = payload.get("config", {})
    _validate_source_config(cfg)
    try:
        sid = add_custom_source(payload["kind"], payload.get("name") or payload["kind"], cfg)
    except (ValueError, KeyError) as exc:
        raise bad(str(exc), 422)
    return {"id": sid}


@app.delete("/api/sources/{source_id}")
def del_source(source_id: str) -> dict[str, bool]:
    delete_custom_source(source_id)
    return {"ok": True}


@app.post("/api/sources/{source_id}/health")
async def source_health(source_id: str) -> dict[str, Any]:
    from .job_source_registry import record_result

    conn = get_connector(source_id)
    if not conn:
        raise bad("Unknown source", 404)
    http = HttpClient(max_retries=1, cache_ttl=0)  # a health check must hit the live endpoint
    conn.prepare(http)
    try:
        ok, msg = await asyncio.wait_for(conn.health_check(http), timeout=90)
    except Exception as exc:
        ok, msg = False, f"{type(exc).__name__}: {exc}"
    finally:
        await http.aclose()
    record_result(source_id, ok=ok, error=None if ok else msg, health="needs_key" if "Needs configuration" in msg else None)
    db.execute("UPDATE sources SET last_error=? WHERE id=?", (msg[:500], source_id))
    return {"ok": ok, "message": msg}


@app.get("/api/sources/suggested")
def suggested_boards() -> list[dict[str, Any]]:
    from .job_source_registry import discover_boards

    return discover_boards()


@app.post("/api/sources/suggested")
def act_on_suggestion(payload: dict = Body(...)) -> dict[str, bool]:
    from .job_source_registry import add_board, dismiss_board

    kind, token = str(payload.get("kind", "")), str(payload.get("token", ""))
    if not re.fullmatch(r"[A-Za-z0-9._%-]{1,80}", token):
        raise bad("Invalid board name", 422)
    try:
        if payload.get("action") == "dismiss":
            dismiss_board(kind, token)
        else:
            add_board(kind, token, str(payload.get("company") or token)[:80])
    except (ValueError, KeyError) as exc:
        raise bad(str(exc), 422)
    return {"ok": True}


@app.get("/api/manual-links")
def get_manual_links(q: str = "Hindi translator") -> list[dict[str, str]]:
    return manual_links(q)


# ---------------------------------------------------------------- settings ----
@app.get("/api/settings")
def get_settings(request: Request = None) -> dict[str, Any]:
    return {
        "weights": get_weights(),
        "default_weights": DEFAULT_WEIGHTS,
        "secrets": settings_store.status(),
        "use_embeddings": bool(db.get_setting("use_embeddings", False)),
        "schedule": scheduler.get_schedule(),
        "form_assist_domains": form_assist.approved_domains(),
        "form_assist_available": form_assist.available(),
        "gmail_redirect_uri": gmail_oauth.redirect_uri(_base_url(request) if request else None),
    }


@app.put("/api/settings")
def put_settings(payload: dict = Body(...)) -> dict[str, Any]:
    if "weights" in payload:
        w = {k: float(v) for k, v in payload["weights"].items() if k in DEFAULT_WEIGHTS}
        if any(v < 0 for v in w.values()) or sum(w.values()) <= 0:
            raise bad("Weights must be non-negative and not all zero", 422)
        db.set_setting("match_weights", w)
    if "use_embeddings" in payload:
        db.set_setting("use_embeddings", bool(payload["use_embeddings"]))
    if "schedule" in payload:
        db.set_setting("schedule", {**scheduler.DEFAULT, **payload["schedule"]})
    if "form_assist_domains" in payload:
        db.set_setting("form_assist_domains", [d.strip().lower() for d in payload["form_assist_domains"] if d.strip()])
    log("settings", "updated", keys=list(payload))
    return get_settings()


@app.put("/api/secrets")
def put_secret(payload: dict = Body(...)) -> dict[str, Any]:
    try:
        settings_store.set_secret(payload["name"], str(payload.get("value", "")))
    except (ValueError, KeyError) as exc:
        raise bad(str(exc), 422)
    log("settings", "secret_updated", name=payload.get("name"))
    return {"ok": True, "secrets": settings_store.status()}


@app.get("/api/audit")
def audit(category: Optional[str] = None, limit: int = 200) -> list[dict[str, Any]]:
    return recent(limit, category)


@app.exception_handler(Exception)
async def unhandled(_, exc: Exception) -> JSONResponse:  # pragma: no cover
    log("app", "error", error=type(exc).__name__)
    return JSONResponse({"detail": f"Internal error: {type(exc).__name__}"}, status_code=500)
