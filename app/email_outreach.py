"""Email outreach with three explicit modes and hard safety rails.

Modes
  draft_only (default) – messages are drafted, never sent.
  review              – drafts wait for your approval; approved ones are sent.
  auto                – messages that pass ALL your rules are queued and sent
                        without per-message approval. Can only be switched on
                        after an email account and rules are configured.

Always enforced (regardless of mode)
  * Recipients must be addresses published in the job posting (or typed by you);
    addresses are never guessed or scraped from elsewhere.
  * Do-not-contact list (opt-outs), blocked companies/recipients, postings that
    say "no emails/agencies".
  * Daily and per-company caps, quiet hours, max follow-ups.
  * Near-identical bodies are blocked (no generic mass mail).
  * Emergency STOP cancels everything queued and blocks sending until you resume.
  * Credentials/tokens are never logged.
"""
from __future__ import annotations

import asyncio
import base64
import mimetypes
import re
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from rapidfuzz import fuzz

from . import config, db
from .application_manager import cover_letter, public_contacts
from .audit_log import log
from .config import get_secret
from .models import Profile
from .profile_store import get_profile
from .resume_parser import derive_target_roles
from . import taxonomy as T

MODES = ("draft_only", "review", "auto")
HARD_MAX_PER_DAY = 25
DEFAULT_SETTINGS: dict[str, Any] = {
    "mode": "draft_only",
    "provider": "gmail" if config.CLOUD else "smtp",  # smtp | gmail (free cloud hosts block SMTP)
    "approved_categories": [],
    "min_score": 75,
    "recipient_types": ["careers_inbox", "recruiter"],
    "max_per_day": 5,
    "max_per_company": 1,
    "templates": ["application", "inquiry", "followup"],
    "attach_resume": False,
    "followups_allowed": False,
    "max_followups": 1,
    "followup_after_days": 7,
    "blocked_companies": [],
    "blocked_recipients": [],
    "quiet_hours": {"enabled": True, "start": "21:00", "end": "09:00", "tz": "Asia/Kolkata"},
    "stop_conditions": {"max_consecutive_failures": 3, "max_failures_per_day": 5},
    "only_when_posting_invites_email": True,
}
OPT_OUT_LINE = "If you'd prefer I don't follow up, just reply \"no thanks\" and I won't contact you again."


class OutreachError(Exception):
    pass


# --------------------------------------------------------------- settings ----

def get_settings() -> dict[str, Any]:
    s = {**DEFAULT_SETTINGS, **(db.get_setting("outreach", {}) or {})}
    s["stopped"] = bool(db.get_setting("outreach_stopped", False))
    s["account_configured"] = account_configured(s["provider"])
    s["from_email"] = sender_address(s["provider"])
    return s


def account_configured(provider: str) -> bool:
    if provider == "gmail":
        return bool(get_secret("GMAIL_CLIENT_ID") and get_secret("GMAIL_TOKEN_REFRESH"))
    return bool(get_secret("SMTP_HOST") and get_secret("SMTP_USER") and get_secret("SMTP_PASSWORD"))


def sender_address(provider: str) -> str:
    if provider == "gmail":
        return get_secret("GMAIL_TOKEN_EMAIL") or ""
    return get_secret("SMTP_USER") or ""


def save_settings(new: dict[str, Any]) -> dict[str, Any]:
    cur = {**DEFAULT_SETTINGS, **(db.get_setting("outreach", {}) or {})}
    for k in DEFAULT_SETTINGS:
        if k in new:
            cur[k] = new[k]
    if cur["mode"] not in MODES:
        raise OutreachError("Unknown mode")
    cur["max_per_day"] = max(0, min(int(cur["max_per_day"]), HARD_MAX_PER_DAY))
    cur["max_per_company"] = max(0, min(int(cur["max_per_company"]), 3))
    cur["max_followups"] = max(0, min(int(cur["max_followups"]), 2))
    cur["min_score"] = max(0, min(int(cur["min_score"]), 100))
    if cur["mode"] == "auto":
        problems = []
        if not account_configured(cur["provider"]):
            problems.append("connect an email account")
        if not cur["approved_categories"]:
            problems.append("choose at least one approved job category")
        if cur["min_score"] < 60:
            problems.append("set minimum match score to 60 or higher")
        if not cur["recipient_types"]:
            problems.append("choose approved recipient types")
        if cur["max_per_day"] < 1:
            problems.append("set a daily maximum of at least 1")
        if problems:
            raise OutreachError("Automatic sending needs you to: " + "; ".join(problems))
    db.set_setting("outreach", cur)
    log("outreach", "settings_saved", mode=cur["mode"], max_per_day=cur["max_per_day"], min_score=cur["min_score"])
    return get_settings()


# ---------------------------------------------------------------- STOP ----

def emergency_stop(reason: str = "Stopped by you") -> int:
    db.set_setting("outreach_stopped", True)
    n = db.execute(
        "UPDATE outreach SET status='cancelled', error=? WHERE status IN ('queued','pending_review')", (f"STOP: {reason}",)
    )
    log("outreach", "EMERGENCY_STOP", reason=reason, cancelled=n)
    return n or 0


def resume_sending() -> None:
    db.set_setting("outreach_stopped", False)
    log("outreach", "resumed")


# ------------------------------------------------------------ suppression ----

def opt_out(email: str, reason: str = "Recipient opted out") -> None:
    email = email.strip().lower()
    db.execute("INSERT INTO suppression(email,reason,created_at) VALUES(?,?,?) ON CONFLICT(email) DO UPDATE SET reason=excluded.reason", (email, reason, db.now_iso()))
    db.execute("UPDATE outreach SET status='cancelled', error='Recipient opted out' WHERE lower(recipient)=? AND status IN ('queued','pending_review','draft')", (email,))
    log("outreach", "opt_out", email_domain=email.split("@")[-1])


def is_suppressed(email: str) -> bool:
    return db.query_one("SELECT 1 FROM suppression WHERE email=?", (email.strip().lower(),)) is not None


# --------------------------------------------------------------- drafting ----

def job_categories(job: dict[str, Any]) -> list[str]:
    text = f"{job['title']} {job.get('description') or ''}"
    skills = T.find(text, "skills")
    langs = [{"language": "Hindi", "level": ""}] if re.search(r"\bhindi\b", text, re.I) else []
    return derive_target_roles(Profile(skills=skills, languages=langs))


def _key_requirement(job: dict[str, Any], matched: list[str]) -> str | None:
    desc = job.get("description") or ""
    for line in re.split(r"[\n•]", desc):
        line = line.strip(" -*·")
        if 25 < len(line) < 180 and any(re.search(re.escape(s.split(" ")[0]), line, re.I) for s in matched):
            return line.rstrip(".")
    return None


def build_message(job: dict[str, Any], template: str, p: Profile, recipient: str, followup_of: dict | None = None) -> tuple[str, str]:
    m = db.jload(job.get("match_detail"), {}) or {}
    matched = [s for s in m.get("matched_skills", []) if not s.startswith("Tool:")]
    name = p.name or "[Your name]"
    company = job.get("company") or "your team"
    title = job["title"]
    years = f"{p.total_years_experience:g} years of " if p.total_years_experience else ""
    if template == "application":
        subject = f"Application: {title}"
        body = cover_letter(job, p) + f"\n\n{OPT_OUT_LINE}"
    elif template == "followup":
        sent = (followup_of or {}).get("sent_at", "")[:10]
        subject = f"Re: {(followup_of or {}).get('subject') or title}"
        body = (
            f"Hello,\n\nI'm following up on my note of {sent} about the {title} role at {company}. "
            f"I remain very interested and can share a sample of my {matched[0].lower() if matched else 'Hindi language'} work "
            f"or complete a short test if helpful.\n\nThank you for your time,\n{name}\n\n{OPT_OUT_LINE}"
        )
    else:  # inquiry
        req = _key_requirement(job, matched)
        subject = f"{title} — Hindi language specialist"
        body = (
            f"Hello,\n\nI saw the {title} opening at {company}"
            + (f", and in particular the need for: \"{req}\"" if req else "")
            + f". I have {years}experience in {', '.join(s.lower() for s in matched[:3]) or 'Hindi translation and linguistic QA'}, "
            f"which lines up closely with this role.\n\n"
            f"Would it be possible to be considered, or could you point me to the right person? "
            f"I'm happy to take any assessment you use.\n\nBest regards,\n{name}\n"
            + " · ".join(filter(None, [p.email, p.phone]))
            + f"\n\n{OPT_OUT_LINE}"
        )
    if not p.verified:
        body = "[PROVISIONAL PROFILE — upload your resume and regenerate before sending]\n\n" + body
    return subject, body


def create_draft(job_id: str, template: str = "inquiry", recipient: str | None = None,
                 recipient_source: str | None = None, auto: bool = False) -> dict[str, Any]:
    job = db.query_one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if not job:
        raise OutreachError("Job not found")
    s = get_settings()
    contacts = public_contacts(job)
    rtype = "entered_by_you"
    if recipient:
        recipient = recipient.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", recipient):
            raise OutreachError("That doesn't look like a valid email address")
        found = next((c for c in contacts["contacts"] if c["email"] == recipient), None)
        if found:
            rtype, recipient_source = found["type"], found["source"]
        else:
            recipient_source = recipient_source or "Entered by you"
    else:
        allowed = [c for c in contacts["contacts"] if c["type"] in s["recipient_types"]] or contacts["contacts"]
        if not allowed:
            raise OutreachError("No email address is published in this posting. Apply via the official page instead.")
        recipient, rtype, recipient_source = allowed[0]["email"], allowed[0]["type"], allowed[0]["source"]
    profile, _ = get_profile()
    subject, body = build_message(job, template, profile, recipient)
    status = "draft"
    if s["mode"] == "review":
        status = "pending_review"
    if auto and s["mode"] == "auto":
        status = "queued"
    mid = db.execute(
        "INSERT INTO outreach(job_id,company,recipient,recipient_type,recipient_source,subject,body,template,attach_resume,"
        "status,mode,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (job_id, job["company"], recipient, rtype, recipient_source, subject, body, template,
         int(bool(s["attach_resume"])), status, s["mode"], db.now_iso()),
    )
    ok, reason = check_rules(get_message(mid), s, at_send=False)
    if not ok and status in ("queued", "pending_review"):
        db.execute("UPDATE outreach SET status='blocked', error=? WHERE id=?", (reason, mid))
    log("outreach", "draft", id=mid, job_id=job_id, template=template, status=status, recipient_type=rtype)
    return get_message(mid)


def get_message(mid: int) -> dict[str, Any]:
    return db.query_one("SELECT * FROM outreach WHERE id=?", (mid,)) or {}


def list_messages(limit: int = 300) -> list[dict[str, Any]]:
    return db.query("SELECT * FROM outreach ORDER BY id DESC LIMIT ?", (limit,))


def update_draft(mid: int, subject: str | None, body: str | None, recipient: str | None) -> dict[str, Any]:
    msg = get_message(mid)
    if not msg or msg["status"] not in ("draft", "pending_review", "blocked"):
        raise OutreachError("Only unsent drafts can be edited")
    db.execute(
        "UPDATE outreach SET subject=COALESCE(?,subject), body=COALESCE(?,body), recipient=COALESCE(?,recipient), "
        "recipient_source=CASE WHEN ? IS NULL THEN recipient_source ELSE 'Entered by you' END WHERE id=?",
        (subject, body, recipient, recipient, mid),
    )
    return get_message(mid)


def approve(mid: int) -> dict[str, Any]:
    s = get_settings()
    if s["mode"] == "draft_only":
        raise OutreachError("Outreach is in Draft Only mode. Switch to 'Review before sending' to send approved messages.")
    msg = get_message(mid)
    if msg.get("status") not in ("draft", "pending_review", "blocked"):
        raise OutreachError("Message is not awaiting approval")
    ok, reason = check_rules(msg, s, at_send=False, manual=True)
    if not ok:
        db.execute("UPDATE outreach SET status='blocked', error=? WHERE id=?", (reason, mid))
        raise OutreachError(reason)
    db.execute("UPDATE outreach SET status='queued', error=NULL WHERE id=?", (mid,))
    log("outreach", "approved", id=mid)
    return get_message(mid)


def cancel(mid: int) -> None:
    db.execute("UPDATE outreach SET status='cancelled' WHERE id=? AND status IN ('draft','pending_review','queued','blocked')", (mid,))


# ------------------------------------------------------------------ rules ----

def _today_count(status: str = "sent") -> int:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    return (db.query_one(f"SELECT COUNT(*) n FROM outreach WHERE status=? AND sent_at>=?", (status, start)) or {"n": 0})["n"]


def in_quiet_hours(q: dict[str, Any], now: datetime | None = None) -> bool:
    if not q or not q.get("enabled"):
        return False
    tz = ZoneInfo(q.get("tz") or "Asia/Kolkata")
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    sh, sm = map(int, q.get("start", "21:00").split(":"))
    eh, em = map(int, q.get("end", "09:00").split(":"))
    cur = now.hour * 60 + now.minute
    start, end = sh * 60 + sm, eh * 60 + em
    return (start <= cur < end) if start < end else (cur >= start or cur < end)


def check_rules(msg: dict[str, Any], s: dict[str, Any], *, at_send: bool, manual: bool = False) -> tuple[bool, str]:
    if not msg:
        return False, "Message not found"
    if s["stopped"]:
        return False, "Emergency STOP is active"
    rcpt = (msg.get("recipient") or "").lower()
    company = (msg.get("company") or "").lower()
    if is_suppressed(rcpt):
        return False, "Recipient is on your do-not-contact list"
    if any(b.strip().lower() and b.strip().lower() in rcpt for b in s["blocked_recipients"]):
        return False, "Recipient is blocked in your rules"
    if any(b.strip().lower() and b.strip().lower() in company for b in s["blocked_companies"]):
        return False, "Company is blocked in your rules"
    job = db.query_one("SELECT * FROM jobs WHERE id=?", (msg.get("job_id"),)) if msg.get("job_id") else None
    if job:
        pc = public_contacts(job)
        if pc["posting_discourages_email"]:
            return False, "The posting asks candidates not to email / to apply only through the portal"
        if not manual:
            if (job.get("match_score") or 0) < s["min_score"]:
                return False, f"Match score {job.get('match_score')} is below your minimum {s['min_score']}"
            if s["approved_categories"] and not set(job_categories(job)) & set(s["approved_categories"]):
                return False, "Job is not in your approved categories"
            if msg.get("recipient_type") not in s["recipient_types"]:
                return False, f"Recipient type '{msg.get('recipient_type')}' is not approved"
            if s.get("only_when_posting_invites_email") and msg.get("template") == "application":
                if not any(c["apply_by_email"] for c in pc["contacts"] if c["email"] == rcpt):
                    return False, "Posting doesn't invite applications by email"
    if msg.get("template") not in s["templates"] and not manual:
        return False, f"Template '{msg.get('template')}' is not permitted"
    if msg.get("followup_number", 0) > 0:
        if not s["followups_allowed"]:
            return False, "Follow-ups are disabled"
        if msg["followup_number"] > s["max_followups"]:
            return False, "Maximum follow-ups reached"
    # same company cap (counts sent + queued, excluding this message)
    n_company = (db.query_one(
        "SELECT COUNT(*) n FROM outreach WHERE lower(company)=? AND status IN ('sent','queued') AND followup_number=0 AND id!=?",
        (company, msg.get("id") or -1),
    ) or {"n": 0})["n"]
    if msg.get("followup_number", 0) == 0 and company and n_company >= s["max_per_company"]:
        return False, f"Already contacted {msg.get('company')} ({n_company}) — per-company limit reached"
    # duplicate outreach to same recipient for same job
    dup = db.query_one(
        "SELECT id FROM outreach WHERE job_id=? AND lower(recipient)=? AND status IN ('sent','queued') AND followup_number=? AND id!=?",
        (msg.get("job_id"), rcpt, msg.get("followup_number", 0), msg.get("id") or -1),
    )
    if dup:
        return False, "Duplicate: this recipient was already contacted about this job"
    # generic-content guard
    for prev in db.query("SELECT body FROM outreach WHERE status='sent' ORDER BY id DESC LIMIT 50"):
        if fuzz.ratio(prev["body"], msg.get("body") or "") > 92:
            return False, "Message is nearly identical to one already sent — personalise it first"
    if "[PROVISIONAL PROFILE" in (msg.get("body") or "") or "[DRAFT FROM PROVISIONAL" in (msg.get("body") or ""):
        return False, "Draft was generated from the provisional profile — upload your resume and regenerate"
    if at_send:
        if not account_configured(s["provider"]):
            return False, "No email account configured"
        if _today_count() >= s["max_per_day"]:
            return False, "Daily sending limit reached"
        if in_quiet_hours(s["quiet_hours"]):
            return False, "Quiet hours — will send later"
    return True, "OK"


# ---------------------------------------------------------------- sending ----

def _resume_attachment() -> tuple[str, bytes] | None:
    stored = db.get_file("resume")
    if stored:
        name, data = stored
        ext = name.rsplit(".", 1)[-1] if "." in name else "pdf"
        return f"Resume.{ext}", data
    for f in sorted(config.UPLOAD_DIR.glob("resume.*")):
        return f.name.replace("resume", "Resume"), f.read_bytes()
    return None


def _build_email(msg: dict[str, Any], sender: str, attach: bool) -> EmailMessage:
    em = EmailMessage()
    from_name = get_secret("SMTP_FROM_NAME") or ""
    em["From"] = f"{from_name} <{sender}>" if from_name else sender
    em["To"] = msg["recipient"]
    em["Subject"] = msg["subject"]
    em.set_content(msg["body"])
    if attach:
        att = _resume_attachment()
        if att:
            name, data = att
            ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
            maintype, subtype = ctype.split("/", 1)
            em.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return em


def _send_smtp(em: EmailMessage) -> None:
    host = get_secret("SMTP_HOST")
    port = int(get_secret("SMTP_PORT") or 587)
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as s:
            s.login(get_secret("SMTP_USER"), get_secret("SMTP_PASSWORD"))
            s.send_message(em)
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls(context=ctx)
            s.login(get_secret("SMTP_USER"), get_secret("SMTP_PASSWORD"))
            s.send_message(em)


async def _send_gmail(em: EmailMessage) -> None:
    from .gmail_oauth import access_token

    token = await access_token()
    raw = base64.urlsafe_b64encode(em.as_bytes()).decode()
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            headers={"Authorization": f"Bearer {token}"},
            json={"raw": raw},
        )
    if r.status_code >= 300:
        raise OutreachError(f"Gmail API HTTP {r.status_code}")


# Tests can replace this to avoid real network sends.
SENDER_OVERRIDE = None


async def send_one(msg: dict[str, Any], s: dict[str, Any]) -> tuple[bool, str]:
    ok, reason = check_rules(msg, s, at_send=True, manual=msg.get("mode") != "auto")
    if not ok:
        return False, reason
    em = _build_email(msg, s["from_email"] or "me@example.invalid", bool(msg.get("attach_resume")))
    try:
        if SENDER_OVERRIDE:
            await SENDER_OVERRIDE(em)
        elif s["provider"] == "gmail":
            await _send_gmail(em)
        else:
            await asyncio.to_thread(_send_smtp, em)
    except Exception as exc:
        return False, f"Send failed: {type(exc).__name__}"
    return True, "sent"


async def process_queue() -> dict[str, int]:
    """Send queued messages that pass the rules. Called every minute."""
    s = get_settings()
    result = {"sent": 0, "failed": 0, "deferred": 0}
    if s["stopped"] or s["mode"] == "draft_only":
        return result
    consecutive_failures = 0
    for msg in db.query("SELECT * FROM outreach WHERE status='queued' ORDER BY id"):
        s = get_settings()  # re-read: STOP may have been pressed mid-loop
        if s["stopped"]:
            break
        ok, reason = await send_one(msg, s)
        if ok:
            db.execute("UPDATE outreach SET status='sent', sent_at=?, error=NULL WHERE id=?", (db.now_iso(), msg["id"]))
            _mark_tracker(msg)
            log("outreach", "sent", id=msg["id"], company=msg["company"], template=msg["template"])
            result["sent"] += 1
            consecutive_failures = 0
        elif reason.startswith(("Quiet hours", "Daily sending limit")):
            result["deferred"] += 1
            break
        elif reason.startswith("Send failed"):
            db.execute("UPDATE outreach SET status='failed', error=? WHERE id=?", (reason, msg["id"]))
            log("outreach", "failed", id=msg["id"], error=reason)
            result["failed"] += 1
            consecutive_failures += 1
            sc = s["stop_conditions"]
            failed_today = (db.query_one(
                "SELECT COUNT(*) n FROM outreach WHERE status='failed' AND created_at>=?",
                (datetime.now(timezone.utc).replace(hour=0, minute=0).isoformat(),)) or {"n": 0})["n"]
            if consecutive_failures >= sc.get("max_consecutive_failures", 3) or failed_today >= sc.get("max_failures_per_day", 5):
                emergency_stop("Automatic stop: too many delivery failures")
                break
        else:
            db.execute("UPDATE outreach SET status='blocked', error=? WHERE id=?", (reason, msg["id"]))
    return result


def _mark_tracker(msg: dict[str, Any]) -> None:
    if not msg.get("job_id") or msg.get("followup_number"):
        return
    from .job_tracker import DuplicateApplicationError, set_status

    try:
        set_status(msg["job_id"], "Email Sent", method=f"Email ({msg['template']})", notes=f"To {msg['recipient']}")
    except DuplicateApplicationError:
        pass


# --------------------------------------------------------------- automation ----

def auto_outreach_for_session(session_id: str) -> int:
    """After a search: queue personalised emails for jobs that pass every rule (auto mode only)."""
    s = get_settings()
    if s["mode"] != "auto" or s["stopped"]:
        return 0
    profile, _ = get_profile()
    if not profile.verified:
        return 0
    n = 0
    jobs = db.query(
        "SELECT * FROM jobs WHERE last_session_id=? AND status='active' AND hidden=0 AND match_label!='Not Eligible' "
        "AND match_score>=? ORDER BY match_score DESC LIMIT 50",
        (session_id, s["min_score"]),
    )
    for job in jobs:
        contacts = public_contacts(job)
        if contacts["posting_discourages_email"]:
            continue
        for c in contacts["contacts"]:
            if c["type"] not in s["recipient_types"] or is_suppressed(c["email"]):
                continue
            template = "application" if c["apply_by_email"] and "application" in s["templates"] else "inquiry"
            if template not in s["templates"]:
                continue
            try:
                msg = create_draft(job["id"], template, c["email"], c["source"], auto=True)
            except OutreachError:
                continue
            if msg.get("status") == "queued":
                n += 1
            break
    queue_followups(s)
    log("outreach", "auto_queued", session=session_id, count=n)
    return n


def queue_followups(s: dict[str, Any] | None = None) -> int:
    s = s or get_settings()
    if not s["followups_allowed"] or s["stopped"] or s["mode"] == "draft_only":
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=int(s["followup_after_days"]))).isoformat()
    n = 0
    for orig in db.query("SELECT * FROM outreach WHERE status='sent' AND followup_number=0 AND sent_at<=?", (cutoff,)):
        app = db.query_one("SELECT status FROM applications WHERE job_id=? ORDER BY attempt DESC LIMIT 1", (orig["job_id"],))
        if app and app["status"] in ("Interview", "Assessment", "Rejected", "Offer Received", "Closed"):
            continue  # they replied / outcome known
        count = (db.query_one("SELECT COUNT(*) n FROM outreach WHERE followup_of=?", (orig["id"],)) or {"n": 0})["n"]
        if count >= s["max_followups"] or is_suppressed(orig["recipient"]):
            continue
        job = db.query_one("SELECT * FROM jobs WHERE id=?", (orig["job_id"],))
        if not job:
            continue
        profile, _ = get_profile()
        subject, body = build_message(job, "followup", profile, orig["recipient"], followup_of=orig)
        status = "queued" if s["mode"] == "auto" else "pending_review"
        db.execute(
            "INSERT INTO outreach(job_id,company,recipient,recipient_type,recipient_source,subject,body,template,attach_resume,"
            "status,mode,followup_of,followup_number,created_at) VALUES(?,?,?,?,?,?,?,?,0,?,?,?,?,?)",
            (orig["job_id"], orig["company"], orig["recipient"], orig["recipient_type"], orig["recipient_source"],
             subject, body, "followup", status, s["mode"], orig["id"], count + 1, db.now_iso()),
        )
        n += 1
    return n


async def outreach_loop(stop_event: asyncio.Event, interval: int = 60) -> None:
    while not stop_event.is_set():
        try:
            await process_queue()
        except Exception as exc:  # never crash the server loop
            log("outreach", "loop_error", error=type(exc).__name__)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
