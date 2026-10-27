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
