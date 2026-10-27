"""Local resume parsing (PDF, DOCX, TXT/MD). Nothing is sent to third parties.

The parser extracts only what is actually written in the resume. Anything it
cannot find stays empty — it never invents employers, skills or degrees.
"""
from __future__ import annotations

import io
import re
from datetime import date
from pathlib import Path

from . import taxonomy as T
from .models import Profile

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}

SECTION_HEADINGS = {
    "summary": r"(professional )?(summary|profile|objective|about me|career objective)",
    "experience": r"(work |professional |employment |relevant )?(experience|history|employment)|career history",
    "education": r"education(al)?( qualifications?| background)?|academics?|qualifications?",
    "skills": r"(key |core |technical |professional )?(skills|competenc(ies|e)|expertise|strengths)",
    "tools": r"(tools|software|technolog(y|ies)|cat tools|platforms)( used| known| & platforms)?",
    "languages": r"languages?( known| proficiency)?",
    "certifications": r"certifications?|certificates?|licen[cs]es|trainings?|courses",
    "projects": r"(key |notable )?projects?",
    "achievements": r"achievements?|accomplishments?|awards?( and recognition)?|honou?rs",
}
_HEAD_RE = {k: re.compile(rf"^\s*({v})\s*[:\-–]?\s*$", re.I) for k, v in SECTION_HEADINGS.items()}

TITLE_WORDS = (
    r"translator|linguist|reviewer|evaluator|annotator|editor|proofreader|transcriber|transcriptionist|"
    r"localizer|localiser|specialist|lead|coordinator|manager|writer|journalist|reporter|analyst|trainer|"
    r"rater|executive|associate|consultant|freelancer|tester|moderator|expert|officer|intern|correspondent|"
    r"sub[- ]?editor|copywriter|content creator"
)
TITLE_RE = re.compile(rf"\b({TITLE_WORDS})\b", re.I)

DATE_TOKEN = r"(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s*,?\s*)?(?:\d{1,2}[/-])?(?:19|20)\d{2}"
RANGE_RE = re.compile(
    rf"(?P<start>{DATE_TOKEN})\s*(?:-|–|—|to|till|until)\s*(?P<end>{DATE_TOKEN}|present|current|now|till date|ongoing|date)",
    re.I,
)
DEGREE_RE = re.compile(
    r"\b(ph\.?d|doctorate|m\.?\s?phil|master'?s?|m\.?\s?a\.?|m\.?\s?sc|mba|mjmc|mjc|m\.?\s?j\.?\s?m\.?\s?c|"
    r"bachelor'?s?|b\.?\s?a\.?|b\.?\s?sc|b\.?\s?com|bjmc|b\.?\s?j\.?\s?m\.?\s?c|bmm|pg diploma|post[- ]graduate diploma|"
    r"diploma|certificate course|12th|10th|higher secondary|senior secondary)\b",
    re.I,
)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\+?\d[\d\s\-().]{8,17}\d")
YEARS_STATEMENT = re.compile(r"(\d{1,2}(?:\.\d)?)\s*\+?\s*(?:years|yrs)(?:\s+of)?\s+(?:\w+\s+){0,4}?experience", re.I)
LEVEL_WORDS = re.compile(r"\b(native|mother tongue|bilingual|fluent|professional|proficient|advanced|intermediate|basic|elementary|c2|c1|b2|b1)\b", re.I)


class ResumeParseError(ValueError):
    pass


# --------------------------------------------------------------- extraction ----

def extract_text(filename: str, data: bytes) -> str:
    ext = Path(filename).suffix.lower()
    if not data:
        raise ResumeParseError("The file is empty")
    if len(data) > 10 * 1024 * 1024:
        raise ResumeParseError("File too large (max 10 MB)")
    try:
        if ext == ".pdf":
            from pypdf import PdfReader  # pure Python — installs everywhere, incl. Windows on ARM

            reader = PdfReader(io.BytesIO(data))
            text = "\n".join((pg.extract_text() or "") for pg in reader.pages[:20])
        elif ext == ".docx":
            import docx

            d = docx.Document(io.BytesIO(data))
            parts = [p.text for p in d.paragraphs]
            for table in d.tables:
                for row in table.rows:
                    parts.append(" | ".join(c.text for c in row.cells))
            text = "\n".join(parts)
        elif ext in (".txt", ".md", ".text"):
            text = data.decode("utf-8", errors="replace")
        else:
            raise ResumeParseError("Unsupported format — upload PDF, DOCX or TXT")
    except ResumeParseError:
        raise
    except Exception as exc:
        raise ResumeParseError(f"Could not read the file ({type(exc).__name__})") from exc
    text = text.replace("\r", "")
    if len(re.sub(r"\s", "", text)) < 80:
        raise ResumeParseError(
            "Very little text could be extracted. If this is a scanned PDF, export it as a text PDF or DOCX."
        )
    return text


# ----------------------------------------------------------------- helpers ----

def split_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {"_top": []}
    current = "_top"
    for line in text.split("\n"):
        stripped = line.strip().strip("•●▪*").strip()
        if not stripped:
            continue
        matched = None
        if len(stripped) <= 45:
            for key, pat in _HEAD_RE.items():
                if pat.match(stripped):
                    matched = key
                    break
        if matched:
            current = matched
            sections.setdefault(current, [])
        else:
            sections.setdefault(current, []).append(stripped)
    return sections


def _parse_date(token: str, is_end: bool) -> date | None:
    t = token.strip().lower()
    if t in ("present", "current", "now", "till date", "ongoing", "date"):
        return date.today()
    year_m = re.search(r"(19|20)\d{2}", t)
    if not year_m:
        return None
    year = int(year_m.group(0))
    month = 12 if is_end else 1
    mon_m = re.match(r"([a-z]{3})", t)
    if mon_m and mon_m.group(1) in MONTHS:
        month = MONTHS[mon_m.group(1)]
    else:
        num_m = re.match(r"(\d{1,2})[/-]", t)
        if num_m and 1 <= int(num_m.group(1)) <= 12:
            month = int(num_m.group(1))
    return date(year, month, 1)


def date_ranges(lines: list[str]) -> list[tuple[date, date, str]]:
    out = []
    for line in lines:
        for m in RANGE_RE.finditer(line):
            s, e = _parse_date(m.group("start"), False), _parse_date(m.group("end"), True)
            if s and e and s <= e and s.year >= 1970:
                out.append((s, e, line))
    return out


def merged_years(ranges: list[tuple[date, date, str]]) -> float:
    spans = sorted((s, e) for s, e, _ in ranges)
    merged: list[list[date]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    months = sum((e.year - s.year) * 12 + (e.month - s.month) + 1 for s, e in merged)
    return round(months / 12, 1)


def _dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for i in items:
        k = i.lower().strip()
        if k and k not in seen:
            seen.add(k)
            out.append(i.strip())
    return out


# ------------------------------------------------------------------- parse ----

def parse_resume_text(text: str) -> Profile:
    sections = split_sections(text)
    full = text
    p = Profile(verified=True)
    lines_top = sections.get("_top", [])

    # Contact
    email = EMAIL_RE.search(full)
    p.email = email.group(0) if email else ""
    for m in PHONE_RE.finditer(full):
        digits = re.sub(r"\D", "", m.group(0))
        if 10 <= len(digits) <= 13:
            p.phone = m.group(0).strip()
            break
    for line in lines_top[:6]:
        if EMAIL_RE.search(line) or re.search(r"\d{5}", line) or len(line) > 50:
            continue
        if re.match(r"^[A-Za-z][A-Za-z.\s'-]{2,48}$", line) and not TITLE_RE.search(line):
            p.name = line.title() if line.isupper() else line
            break
    for line in lines_top[:8]:
        if TITLE_RE.search(line) and len(line) < 120 and not EMAIL_RE.search(line):
            p.headline = line
            break
    loc = re.search(r"\b(Ghaziabad|Noida|New Delhi|Delhi|Gurugram|Gurgaon|Mumbai|Bengaluru|Bangalore|Hyderabad|Pune|Chennai|Kolkata|Lucknow|Jaipur|India)\b[^\n|]{0,30}", "\n".join(lines_top[:10]))
    p.location = loc.group(0).strip(" ,|") if loc else ""

    # Summary
    p.summary = " ".join(sections.get("summary", [])[:8])[:1200]

    # Skills / tools / domains across whole text
    p.skills = T.find(full, "skills")
    p.tools = T.find(full, "tools")
    p.industries = T.find(full, "domains")
    explicit_skill_lines = sections.get("skills", []) + sections.get("tools", [])
    extra = []
    for line in explicit_skill_lines:
        for piece in re.split(r"[,;|•●▪/]", line):
            piece = piece.strip(" -–:")
            if 2 < len(piece) < 40 and not re.search(r"\d{4}", piece):
                extra.append(piece)
    p.keywords = _dedupe(p.skills + p.tools + extra)[:80]

    # Languages
    lang_lines = sections.get("languages", [])
    langs = T.find_languages(" ".join(lang_lines)) or T.find_languages(full)
    lang_entries = []
    for lang in langs:
        level = ""
        for line in lang_lines or [full]:
            if re.search(rf"\b{lang}\b", line, re.I):
                lv = LEVEL_WORDS.search(line)
                if lv:
                    level = lv.group(1).title()
                    break
        lang_entries.append({"language": lang, "level": level or "Stated"})
    p.languages = lang_entries

    # Experience
    exp_lines = sections.get("experience", []) or [l for l in text.split("\n") if l.strip()]
    ranges = date_ranges(exp_lines)
    years_from_dates = merged_years(ranges) if ranges else 0.0
    stated = [float(m.group(1)) for m in YEARS_STATEMENT.finditer(full)]
    p.total_years_experience = max([years_from_dates] + stated) if (ranges or stated) else None
    if stated and years_from_dates and abs(max(stated) - years_from_dates) > 2:
        p.notes.append(
            f"Resume states {max(stated)} years; date ranges add up to {years_from_dates} years. Using the larger figure — please verify."
        )

    titles, history = [], []
    for idx, line in enumerate(exp_lines):
        rm = RANGE_RE.search(line)
        if rm or (TITLE_RE.search(line) and len(line) < 110 and not line.endswith(".")):
            # A header-ish line: title / company / dates
            header = RANGE_RE.sub("", line).strip(" |,–-—()")
            if TITLE_RE.search(header) and len(header) < 110:
                titles.append(re.split(r"\s+(?:at|@|\||,|–|—|-)\s+", header)[0].strip())
            if rm:
                context = [exp_lines[i] for i in range(max(0, idx - 2), idx) if len(exp_lines[i]) < 110]
                entry = {
                    "line": header or line,
                    "context": context,
                    "start": rm.group("start"),
                    "end": rm.group("end"),
                }
                splitter = r"\s+(?:at|@|\|)\s+|\s+[–—-]\s+|,\s+"
                parts = [x.strip() for x in re.split(splitter, header) if x.strip()]
                if len(parts) < 2:
                    # dates on their own line: use the nearest header-like line above
                    for cl in reversed(context):
                        if not cl.endswith(".") and ("|" in cl or " at " in cl or TITLE_RE.search(cl)) and not re.search(r"\d+\s*%|\d{2},\d{3}", cl):
                            parts = [x.strip() for x in re.split(splitter, cl) if x.strip()]
                            break
                if len(parts) >= 2:
                    entry["title_guess"], entry["employer_guess"] = parts[0], parts[1]
                    entry["line"] = " | ".join(parts[:2]) + f" ({rm.group('start')} – {rm.group('end')})"
                history.append(entry)
    p.job_titles = _dedupe([t for t in titles if 3 < len(t) < 80])[:15]
    p.work_history = history[:20]

    # Education
    edu = []
    for line in sections.get("education", []) or [l for l in text.split("\n") if DEGREE_RE.search(l)]:
        if DEGREE_RE.search(line) or re.search(r"university|college|institute|school", line, re.I):
            yr = re.findall(r"(?:19|20)\d{2}", line)
            edu.append({"text": line[:200], "year": yr[-1] if yr else None,
                        "field_mass_comm": bool(re.search(r"journalism|mass comm", line, re.I))})
    p.education = edu[:10]

    p.certifications = _dedupe(sections.get("certifications", []))[:20]
    ach = sections.get("achievements", [])
    ach += [l for l in exp_lines if re.search(r"\d+\s*%|\b\d{2,}[,\d]*\s*(words|projects|hours|tasks|languages|files)\b|award|recogni", l, re.I)]
    p.achievements = _dedupe(ach)[:15]

    # Transferable skills
    transfer = []
    if "Journalism" in p.skills or any(e["field_mass_comm"] for e in p.education):
        transfer += ["Research & fact-checking", "Writing to style guides", "Deadline-driven delivery"]
    if "Team lead / reviewer lead" in p.skills or any(re.search(r"lead|manager|coordinator", t, re.I) for t in p.job_titles):
        transfer += ["Team leadership", "Reviewer calibration & feedback"]
    if "Linguistic QA (LQA)" in p.skills or "Proofreading" in p.skills:
        transfer += ["Error categorisation (MQM-style)", "Attention to detail"]
    if "LLM / AI output evaluation" in p.skills or "AI training" in p.skills:
        transfer += ["Rubric-based evaluation", "Guideline adherence"]
    p.transferable_skills = _dedupe(transfer)

    p.target_roles = derive_target_roles(p)
    if not p.languages:
        p.notes.append("No languages section found — add 'Languages: Hindi (Native), English (Fluent)' to your resume or edit the profile.")
    if p.total_years_experience is None:
        p.notes.append("Could not determine total years of experience — please set it in the profile editor.")
    return p


def derive_target_roles(p: Profile) -> list[str]:
    s = set(p.skills)
    roles = []
    rules = [
        ({"Translation"}, "English-to-Hindi Translator"),
        ({"Localization"}, "Hindi Localization Specialist"),
        ({"Linguistic QA (LQA)"}, "Hindi Linguistic QA / LQA Reviewer"),
        ({"Proofreading"}, "Hindi Proofreader and Language Editor"),
        ({"Editing"}, "Hindi Proofreader and Language Editor"),
        ({"LLM / AI output evaluation"}, "AI Response Evaluator / AI Model Rater"),
        ({"LLM / AI output evaluation"}, "AI Language Evaluator"),
        ({"AI training"}, "Hindi AI Trainer"),
        ({"Data annotation"}, "AI Data Annotator"),
        ({"Multilingual dataset evaluation"}, "Multilingual Dataset Evaluator"),
        ({"Transcription"}, "Hindi Transcriptionist"),
        ({"Speech / audio data"}, "Audio and Speech Data Evaluator"),
        ({"Content moderation / AI safety"}, "AI Safety and Content Quality Evaluator"),
        ({"Localization testing"}, "Localization QA Tester"),
        ({"Team lead / reviewer lead"}, "Language Quality Lead"),
        ({"Project coordination"}, "Hindi Linguistic Project Coordinator"),
        ({"Red teaming"}, "Prompt Evaluator / Multilingual Red Teaming"),
        ({"Search quality evaluation"}, "Search Quality Evaluation"),
        ({"Journalism"}, "Hindi Technical / Content Writing"),
        ({"Content writing"}, "Hindi Technical / Content Writing"),
    ]
    for need, role in rules:
        if need <= s and role not in roles:
            roles.append(role)
    if any(l["language"] == "Hindi" for l in p.languages) and "Hindi Language Specialist" not in roles:
        roles.append("Hindi Language Specialist")
    return roles


def parse_resume(filename: str, data: bytes) -> tuple[Profile, str]:
    text = extract_text(filename, data)
    return parse_resume_text(text), text
