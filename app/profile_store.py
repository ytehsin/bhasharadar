"""Profile persistence. Until a resume is uploaded, a *provisional* profile built
only from the background stated in the project brief is used and clearly
labelled as unverified."""
from __future__ import annotations

import json

from . import db
from .audit_log import log
from .models import Profile
from .resume_parser import derive_target_roles

SEED_TEXT = """Provisional profile from the project brief (not a resume).
English-to-Hindi and Hindi-to-English translation and localization.
Hindi linguistic quality assurance (LQA), proofreading, editing, transcreation, and terminology management.
AI training, multilingual dataset evaluation, language model output evaluation, annotation, and linguistic quality assessment.
Hindi text, audio, transcription, and other multilingual content evaluation.
Language quality review, lead reviewer responsibilities, and project coordination.
Experience across fintech, banking, insurance, education, technology, media, and digital products.
Experience using CAT tools, localization platforms, QA tools, and language technology workflows.
Journalism and mass communication education and professional writing experience."""


def seed_profile() -> Profile:
    p = Profile(
        headline="Hindi Language Specialist — Translation, LQA & AI Data Evaluation (provisional)",
        location="India",
        languages=[{"language": "Hindi", "level": "Stated"}, {"language": "English", "level": "Stated"}],
        skills=[
            "Translation", "Localization", "Transcreation", "Linguistic QA (LQA)", "Proofreading", "Editing",
            "Terminology management", "AI training", "Multilingual dataset evaluation", "LLM / AI output evaluation",
            "Data annotation", "Transcription", "Speech / audio data", "Language review", "Team lead / reviewer lead",
            "Project coordination", "Journalism", "Content writing", "Quality control",
        ],
        tools=["CAT tools"],
        industries=["Fintech", "Banking", "Insurance", "Education", "Technology / software", "Media / entertainment"],
        education=[{"text": "Journalism and mass communication (details from resume pending)", "year": None, "field_mass_comm": True}],
        total_years_experience=None,
        verified=False,
        preferences={"work_modes": ["remote", "hybrid", "onsite"], "locations": ["Remote (Worldwide)", "India"]},
        notes=[
            "PROVISIONAL: built only from the background listed in the project brief. Upload your resume to replace it.",
            "Years of experience, employers, specific CAT tools and education details are unknown until a resume is parsed.",
        ],
    )
    p.target_roles = derive_target_roles(p)
    p.keywords = p.skills + p.tools
    return p


def get_profile() -> tuple[Profile, str]:
    row = db.query_one("SELECT * FROM profile WHERE id=1")
    if not row:
        return seed_profile(), SEED_TEXT
    return Profile(**json.loads(row["data"])), row["resume_text"] or SEED_TEXT


def profile_status() -> dict:
    row = db.query_one("SELECT source, resume_filename, updated_at FROM profile WHERE id=1")
    if not row:
        return {"source": "seed", "filename": None, "updated_at": None, "verified": False}
    return {"source": row["source"], "filename": row["resume_filename"], "updated_at": row["updated_at"],
            "verified": row["source"] == "resume"}


def save_profile(p: Profile, *, source: str, filename: str | None = None, resume_text: str | None = None) -> None:
    existing = db.query_one("SELECT resume_text, resume_filename FROM profile WHERE id=1")
    if resume_text is None and existing:
        resume_text = existing["resume_text"]
    if filename is None and existing:
        filename = existing["resume_filename"]
    db.execute(
        "INSERT INTO profile(id,data,source,resume_filename,resume_text,updated_at) VALUES(1,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET data=excluded.data, source=excluded.source, "
        "resume_filename=excluded.resume_filename, resume_text=excluded.resume_text, updated_at=excluded.updated_at",
        (p.model_dump_json(), source, filename, resume_text, db.now_iso()),
    )
    log("profile", "saved", source=source, filename=filename, skills=len(p.skills))


def delete_profile() -> None:
    db.execute("DELETE FROM profile WHERE id=1")
    log("privacy", "resume_deleted")
