"""Application materials: cover letters, role-specific resumes, form answers.

Rules enforced here:
  * Only facts present in your parsed profile/resume are used.
  * Anything that is a material representation (salary, notice period, work
    authorisation, relocation, legal declarations, references, demographic
    data) is NEVER filled in — it is returned as "needs your input".
  * No application is marked submitted without your confirmation.
"""
from __future__ import annotations

import io
import json
import re
from typing import Any

from . import db
from .models import Profile

NEEDS_INPUT_FIELDS = [
    ("salary_expectation", "Expected salary / hourly rate", "Salary commitment — only you can decide"),
    ("notice_period", "Notice period / earliest start date", "Availability commitment"),
    ("work_authorization", "Work authorisation / visa status for this country", "Legal declaration"),
    ("relocation", "Willing to relocate?", "Relocation commitment"),
    ("references", "References", "Never invented"),
    ("demographics", "Gender / disability / diversity questions", "Sensitive personal data — optional and yours to answer"),
    ("legal_declarations", "Background check / criminal record / non-compete declarations", "Legal declaration"),
    ("screening_questions", "Role-specific screening questions", "Answer from your own experience"),
]


def _years_phrase(p: Profile) -> str:
    if p.total_years_experience:
        return f"{p.total_years_experience:g} years of"
    return "hands-on"


def _match(job: dict[str, Any]) -> dict[str, Any]:
    return db.jload(job.get("match_detail"), {}) or {}


def cover_letter(job: dict[str, Any], p: Profile) -> str:
    m = _match(job)
    skills = [s for s in m.get("matched_skills", []) if not s.startswith("Tool:")][:4]
    tools = [s[6:] for s in m.get("matched_skills", []) if s.startswith("Tool:")][:3]
    langs = ", ".join(f"{l['language']}" for l in p.languages) or "Hindi and English"
    domains = [d for d in p.industries][:4]
    name = p.name or "[Your name]"
    company = job.get("company") or "your team"
    lines = []
    if not p.verified:
        lines.append("[DRAFT FROM PROVISIONAL PROFILE — upload your resume and re-generate before sending]\n")
    lines += [
        f"Dear {company} Hiring Team,",
        "",
        f"I am applying for the {job['title']} role. I am a {langs} language professional with {_years_phrase(p)} "
        f"experience in {', '.join(skills[:3]).lower() if skills else 'translation, linguistic quality review and AI language data work'}.",
        "",
    ]
    body = []
    if skills:
        body.append(f"The posting's focus on {', '.join(s.lower() for s in skills)} closely matches the work I do day to day.")
    if domains:
        body.append(f"I have worked on content across {', '.join(d.lower() for d in domains)}.")
    if tools:
        body.append(f"I am comfortable with {', '.join(tools)}.")
    if p.achievements:
        body.append(f"For example: {p.achievements[0].rstrip('.')}.")
    if "Team lead / reviewer lead" in p.skills:
        body.append("I have also acted as lead reviewer, calibrating reviewers and keeping quality consistent across a team.")
    lines.append(" ".join(body) or "My background in Hindi language quality and AI data evaluation fits this role well.")
    lines += [
        "",
        "I would welcome the chance to complete any assessment or sample task you use for this role.",
        "",
        "Kind regards,",
        name,
        " · ".join(filter(None, [p.email, p.phone])),
    ]
    return "\n".join(lines)


def form_answers(job: dict[str, Any], p: Profile) -> dict[str, Any]:
    m = _match(job)
    prefilled = {
        "full_name": p.name or None,
        "email": p.email or None,
        "phone": p.phone or None,
        "location": p.location or None,
        "years_experience": p.total_years_experience,
        "languages": [f"{l['language']} ({l['level']})" for l in p.languages],
        "relevant_skills": [s for s in m.get("matched_skills", [])][:10],
        "why_interested": (
            f"The role combines {', '.join(s.lower() for s in m.get('matched_skills', [])[:3]) or 'Hindi language quality work'}, "
            f"which is the core of my experience." if p.verified else None
        ),
    }
    missing_profile = [k for k, v in prefilled.items() if v in (None, "", [])]
    return {
        "prefilled": prefilled,
        "missing_from_profile": missing_profile,
        "needs_your_input": [{"field": f, "label": l, "why": w} for f, l, w in NEEDS_INPUT_FIELDS],
        "warning": None if p.verified else "Profile is provisional — upload your resume first.",
    }


def tailored_resume_docx(job: dict[str, Any], p: Profile, resume_text: str) -> bytes:
    """Re-orders your OWN resume content for this role. Adds nothing new."""
    if not p.verified:
        raise ValueError("Upload your resume first — a tailored resume can only be built from your real resume.")
    import docx
    from docx.shared import Pt

    m = _match(job)
    matched = [s for s in m.get("matched_skills", []) if not s.startswith("Tool:")]
    ordered_skills = matched + [s for s in p.skills if s not in matched]
    ordered_tools = [s[6:] for s in m.get("matched_skills", []) if s.startswith("Tool:")]
    ordered_tools += [t for t in p.tools if t not in ordered_tools]

    d = docx.Document()
    style = d.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10.5)
    d.add_heading(p.name or "Your Name", level=0)
    d.add_paragraph(" | ".join(filter(None, [p.email, p.phone, p.location])))
    if p.headline:
        d.add_paragraph(p.headline).runs[0].bold = True
    if p.summary:
        d.add_heading("Summary", level=1)
        d.add_paragraph(p.summary)
    d.add_heading(f"Key skills for {job['title'][:60]}", level=1)
    d.add_paragraph(", ".join(ordered_skills))
    if ordered_tools:
        d.add_heading("Tools", level=1)
        d.add_paragraph(", ".join(ordered_tools))
    if p.languages:
        d.add_heading("Languages", level=1)
        d.add_paragraph(", ".join(f"{l['language']} ({l['level']})" for l in p.languages))
    d.add_heading("Experience (from your resume, unchanged)", level=1)
    exp = _section_text(resume_text, "experience")
    for line in (exp or resume_text).split("\n"):
        if line.strip():
            d.add_paragraph(line.strip(), style="List Bullet" if line.strip()[:1] in "•●▪-*" else None)
    if p.education:
        d.add_heading("Education", level=1)
        for e in p.education:
            d.add_paragraph(e["text"])
    if p.certifications:
        d.add_heading("Certifications", level=1)
        for c in p.certifications:
            d.add_paragraph(c)
    out = io.BytesIO()
    d.save(out)
    return out.getvalue()


def _section_text(text: str, name: str) -> str:
    from .resume_parser import split_sections

    return "\n".join(split_sections(text).get(name, []))


# ------------------------------------------------------------------ contacts ----
EMAIL_RE = re.compile(r"(?<![\w.+-])([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})(?![\w-])")
NON_HIRING = re.compile(r"^(no-?reply|donotreply|privacy|legal|abuse|security|support|billing|press|media|unsubscribe|dpo|gdpr)@", re.I)
NO_CONTACT = re.compile(r"(no (calls|emails|agencies)( or (calls|emails))?|do not (contact|email)|please don'?t (email|contact)|apply (only )?(through|via) (the|our) (portal|website|link))", re.I)


def public_contacts(job: dict[str, Any]) -> dict[str, Any]:
    """Email addresses published in the posting itself. Never guessed."""
    text = job.get("description") or ""
    blocked = bool(NO_CONTACT.search(text))
    out = []
    for m in EMAIL_RE.finditer(text):
        email = m.group(1).strip(".").lower()
        if NON_HIRING.match(email) or any(c["email"] == email for c in out):
            continue
        ctx = text[max(0, m.start() - 120): m.end() + 60].lower()
        if re.match(r"^(careers?|jobs?|hr|hiring|talent|recruit\w*|apply|cv|resumes?)@", email):
            rtype = "careers_inbox"
        elif "recruit" in ctx or "talent" in ctx:
            rtype = "recruiter"
        else:
            rtype = "hiring_contact"
        apply_by_email = bool(re.search(r"(send|email|submit|share|mail)\s+(your\s+)?(cv|resume|application|portfolio)", ctx))
        out.append({"email": email, "type": rtype, "source": "Published in job posting", "apply_by_email": apply_by_email})
    return {"contacts": out, "posting_discourages_email": blocked}


# ============================================================== apply kit ====
# Answers you save once in Settings → "Application answers". Only what YOU typed is used for
# commitments (pay, notice, work rights); nothing is inferred.
ANSWER_FIELDS: list[tuple[str, str, str]] = [
    ("linkedin", "LinkedIn URL", "https://www.linkedin.com/in/…"),
    ("website", "Website / portfolio", "https://yasirtehsin.com"),
    ("proz", "ProZ / other profile", ""),
    ("city", "City", "Ghaziabad"),
    ("country", "Country of residence", "India"),
    ("timezone", "Time zone", "IST (UTC+5:30)"),
    ("notice_period", "Notice period / earliest start", "Immediately / 2 weeks"),
    ("availability", "Weekly availability", "20–40 hours/week"),
    ("rate_hourly", "Expected hourly rate", "USD 20/hour"),
    ("salary", "Expected salary (full-time)", "INR … per month"),
    ("work_authorization", "Work authorisation", "Indian citizen, based in India"),
    ("sponsorship", "Need visa sponsorship?", "No"),
    ("relocation", "Willing to relocate?", "No — remote only"),
    ("how_heard", "How did you hear about us?", "Company careers page"),
]


def saved_answers(p: Profile) -> dict[str, str]:
    return {k: str(v).strip() for k, v in ((p.preferences or {}).get("answers") or {}).items() if str(v).strip()}


def _name_parts(p: Profile) -> tuple[str, str]:
    parts = (p.name or "").split()
    return (parts[0], " ".join(parts[1:])) if parts else ("", "")


def detail_fields(p: Profile) -> list[dict[str, str]]:
    a = saved_answers(p)
    first, last = _name_parts(p)
    langs = ", ".join(f"{l['language']} ({l['level']})" for l in p.languages)
    rows = [
        ("First name", first), ("Last name", last), ("Full name", p.name), ("Email", p.email), ("Phone", p.phone),
        ("Location", p.location or ", ".join(x for x in (a.get("city"), a.get("country")) if x)),
        ("LinkedIn", a.get("linkedin")), ("Website", a.get("website")), ("Other profile", a.get("proz")),
        ("Years of experience", f"{p.total_years_experience:g}" if p.total_years_experience else ""),
        ("Languages", langs), ("Time zone", a.get("timezone")), ("Availability", a.get("availability")),
        ("Notice period", a.get("notice_period")), ("Expected rate", a.get("rate_hourly")), ("Expected salary", a.get("salary")),
        ("Work authorisation", a.get("work_authorization")), ("Visa sponsorship needed", a.get("sponsorship")),
        ("Relocation", a.get("relocation")), ("How did you hear", a.get("how_heard")),
    ]
    return [{"label": l, "value": v} for l, v in rows if v]


LEGAL = re.compile(r"(privacy|consent|acknowledg|agree|certif|declar|attest|terms|truthful|accurate|background check|criminal|non[- ]?compete)", re.I)
DEMOGRAPHIC = re.compile(r"(gender|race|ethnic|veteran|disab|pronoun|sexual|religion|caste)", re.I)
OPEN_TEXT = re.compile(r"(why|tell us|describe|motivat|interest|cover letter|about yourself|experience with|example)", re.I)


def _pick_option(values: list[dict], wanted: str) -> str | None:
    w = wanted.lower()
    for v in values:
        lab = str(v.get("label", ""))
        if lab.lower() == w:
            return lab
    for v in values:
        lab = str(v.get("label", ""))
        if w and (w in lab.lower() or lab.lower() in w):
            return lab
    return None


def map_question(q: dict[str, Any], p: Profile, cover: str) -> dict[str, Any]:
    """Suggest an answer for one Greenhouse application question."""
    a = saved_answers(p)
    label = q.get("label", "")
    fields = q.get("fields") or [{}]
    f0 = fields[0]
    name, ftype = f0.get("name", ""), f0.get("type", "")
    values = f0.get("values") or []
    first, last = _name_parts(p)
    out = {"label": label, "required": bool(q.get("required")), "answer": "", "kind": "you", "note": ""}

    def give(val: str | None, kind: str = "auto", note: str = ""):
        if val:
            if values:
                opt = _pick_option(values, val)
                if not opt:
                    out.update(note=f"Choose the option closest to: {val}")
                    return out
                val = opt
            out.update(answer=val, kind=kind, note=note)
        return out

    direct = {"first_name": first, "last_name": last, "email": p.email, "phone": p.phone}
    if name in direct:
        return give(direct[name])
    if name.startswith("resume") or ftype == "input_file" and re.search(r"resume|cv", label, re.I):
        out.update(kind="file", answer="Attach your resume", note="Use the resume download below")
        return out
    if name.startswith("cover_letter"):
        return give(cover, "draft", "Drafted from your resume — read before using")
    if LEGAL.search(label):
        out["note"] = "Read and answer this yourself"
        return out
    if DEMOGRAPHIC.search(label):
        out["note"] = "Optional and personal — your choice"
        return out
    rules = [
        (r"linkedin", a.get("linkedin")),
        (r"website|portfolio|personal site|github", a.get("website")),
        (r"based in (the )?united states|state of residence|u\.?s\.? state", "not located in the United States" if (a.get("country") or "India").lower() != "united states" else None),
        (r"country", a.get("country") or ("India" if re.search(r"india", p.location or "", re.I) else None)),
        (r"\bcity\b|location|where are you (located|based)", p.location or a.get("city")),
        (r"time ?zone", a.get("timezone")),
        (r"notice|start date|when can you start|availability to start", a.get("notice_period")),
        (r"hours|availability|available per week", a.get("availability")),
        (r"hourly|rate", a.get("rate_hourly")),
        (r"salary|compensation|pay expectation|expected pay", a.get("salary") or a.get("rate_hourly")),
        (r"sponsor", a.get("sponsorship")),
        (r"authori[sz]ed|right to work|work permit|eligible to work", a.get("work_authorization")),
        (r"relocat", a.get("relocation")),
        (r"hear about|how did you find|referr", a.get("how_heard")),
        (r"years of (professional )?experience|how many years", f"{p.total_years_experience:g}" if p.total_years_experience else None),
        (r"language|fluen|native|proficien", ", ".join(f"{l['language']} ({l['level']})" for l in p.languages)),
        (r"full name|your name", p.name),
        (r"e-?mail", p.email),
        (r"phone|mobile", p.phone),
    ]
    for pat, val in rules:
        if re.search(pat, label, re.I):
            if val:
                return give(val, "saved" if pat not in (r"full name|your name", r"e-?mail", r"phone|mobile") else "auto")
            out["note"] = "Add this once in Settings → Application answers"
            return out
    if ftype == "textarea" or OPEN_TEXT.search(label):
        para = next((x for x in cover.split("\n\n") if len(x) > 80 and not x.startswith("Dear")), "")
        return give(para, "draft", "Draft from your resume — personalise it")
    return out


def greenhouse_ref(job: dict[str, Any]) -> tuple[str, str, bool] | None:
    """(board token, job id, eu) when the posting lives on a Greenhouse board."""
    tags = job.get("tags")
    tags = json.loads(tags) if isinstance(tags, str) and tags.startswith("[") else (tags or [])
    for t in tags:
        m = re.fullmatch(r"gh:(eu:)?([A-Za-z0-9_-]+)", str(t))
        if m and str(job.get("source_job_id") or "").isdigit():
            return m.group(2), str(job["source_job_id"]), bool(m.group(1))
    for u in (job.get("apply_url"), job.get("url")):
        m = re.search(r"(?:job-boards|boards)(\.eu)?\.greenhouse\.io/([A-Za-z0-9_-]+)/jobs/(\d+)", u or "")
        if m:
            return m.group(2), m.group(3), bool(m.group(1))
    return None


def apply_kit(job: dict[str, Any], p: Profile, questions: list[dict] | None = None) -> dict[str, Any]:
    cover = cover_letter(job, p)
    contacts = public_contacts(job)
    email_ok = [c for c in contacts["contacts"] if c["apply_by_email"]] if not contacts["posting_discourages_email"] else []
    mapped = [map_question(q, p, cover) for q in (questions or [])]
    needs = [m["label"] for m in mapped if m["kind"] == "you" and m["required"]]
    missing_saved = [lab for key, lab, _ in ANSWER_FIELDS if key in ("linkedin", "notice_period", "rate_hourly", "work_authorization")
                     and not saved_answers(p).get(key)]
    return {
        "job": {"id": job["id"], "title": job["title"], "company": job.get("company"), "score": job.get("match_score"),
                "label": job.get("match_label"), "apply_url": job.get("apply_url") or job.get("url"), "url": job.get("url"),
                "source": job.get("source_name")},
        "details": detail_fields(p),
        "questions": mapped,
        "questions_source": "Greenhouse application form" if questions else None,
        "cover_letter": cover,
        "resume_on_file": db.get_file("resume") is not None,
        "email_apply": email_ok[0] if email_ok else None,
        "needs_you": needs,
        "missing_saved_answers": missing_saved,
        "provisional": not p.verified,
    }
