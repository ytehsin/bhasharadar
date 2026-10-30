"""Transparent, configurable job ↔ resume matching.

Score = sum of weighted components (default weights sum to 100):
  skills 30 · experience 20 · responsibilities 15 · language & domain 10 ·
  work arrangement & location 10 · seniority 5 · compensation 5 · qualifications 5

Then hard-eligibility rules apply (required language you don't have, location
restrictions that exclude India, citizenship/clearance) → "Not Eligible" with
the reason shown — never silently dropped. A job that is not a language / AI
data role at all is capped as "Low Match" regardless of title overlap.

Similarity: TF-IDF cosine (scikit-learn) by default; if the optional
`sentence-transformers` package is installed and enabled in Settings, local
embeddings are blended in. No paid API is needed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from rapidfuzz import fuzz

from . import job_normalizer as N
from . import taxonomy as T
from .models import JobRecord, Profile, SearchFilters
from .resume_parser import derive_target_roles

MATCHER_VERSION = "1.3"  # bump when scoring logic changes; stored jobs are re-scored on next start

DEFAULT_WEIGHTS: dict[str, float] = {
    "skills": 30,
    "experience": 20,
    "responsibilities": 15,
    "language_domain": 10,
    "location": 10,
    "seniority": 5,
    "compensation": 5,
    "qualifications": 5,
}
COMPONENT_LABELS = {
    "skills": "Skill match",
    "experience": "Experience match",
    "responsibilities": "Responsibilities match",
    "language_domain": "Language & domain",
    "location": "Work arrangement & location",
    "seniority": "Seniority",
    "compensation": "Compensation",
    "qualifications": "Additional qualifications",
}
# Partial credit: a job skill you don't list but can reach with minimal upskilling
RELATED: dict[str, set[str]] = {
    "Content moderation / AI safety": {"Language review", "LLM / AI output evaluation", "Quality control", "Editing"},
    "Search quality evaluation": {"LLM / AI output evaluation", "Language review", "Data annotation"},
    "Red teaming": {"Prompt writing", "LLM / AI output evaluation"},
    "Prompt writing": {"AI training", "LLM / AI output evaluation", "Content writing"},
    "Localization testing": {"Linguistic QA (LQA)", "Localization", "Quality control"},
    "Subtitling": {"Transcription", "Translation"},
    "Transcription": {"Speech / audio data", "Proofreading"},
    "Speech / audio data": {"Transcription"},
    "Data annotation": {"LLM / AI output evaluation", "Quality control"},
    "Multilingual dataset evaluation": {"LLM / AI output evaluation", "Data annotation", "Linguistic QA (LQA)"},
    "NLP data quality": {"Data annotation", "Linguistic QA (LQA)", "Quality control"},
    "Transcreation": {"Translation", "Content writing"},
    "Terminology management": {"Translation", "Linguistic QA (LQA)"},
    "Vendor management": {"Project coordination"},
    "AI training": {"LLM / AI output evaluation", "Data annotation"},
    "LLM / AI output evaluation": {"AI training", "Linguistic QA (LQA)"},
}
# Postings that look like scams or low-quality "earn from home" ads
CAUTION_PATTERNS: dict[str, re.Pattern] = {
    "Asks you to pay a fee": re.compile(r"(registration|joining|training|processing|security|application|onboarding)\s+(fee|deposit|charges?)|pay\s+(a\s+)?(small\s+)?(fee|amount)\s+(to|for|before)", re.I),
    "Contact only via WhatsApp/Telegram": re.compile(r"(whats\s?app|telegram)\s*(only|me|us|number|@|\+|:)", re.I),
    "Too-good-to-be-true pay claims": re.compile(r"earn\s+(up\s+to\s+)?(₹|rs\.?|inr|\$)\s?\d[\d,]*\s*(per|a|/)\s*(day|week)|no experience (needed|required).{0,60}(earn|income)", re.I),
    "Asks for bank or ID details up front": re.compile(r"(send|share|provide)\s+(your\s+)?(bank (account|details)|aadhaar|pan card|passport copy|otp)", re.I),
    "Personal email domain for a company job": re.compile(r"(apply|send|email)[^\n]{0,40}@(gmail|yahoo|hotmail|outlook|rediffmail)\.com", re.I),
}

# A country named in the title ("… (France)", "… – Australia") usually means you must live there.
TITLE_COUNTRY = re.compile(
    r"(?:\(|[-–—|,]\s*|\bin\s+)(france|germany|italy|netherlands|spain|portugal|poland|ireland|sweden|norway|denmark|finland|"
    r"belgium|austria|switzerland|uk|united kingdom|england|london|usa|us|united states|canada|australia|new zealand|brazil|mexico|"
    r"argentina|colombia|chile|japan|korea|china|singapore|malaysia|philippines|indonesia|vietnam|thailand|uae|dubai|saudi arabia|"
    r"qatar|israel|turkey|egypt|nigeria|kenya|south africa)\b(?![^()]*\bindia\b)",
    re.I,
)

LABEL_THRESHOLDS = [(80, "Excellent Match"), (65, "Strong Match"), (50, "Possible Match"), (0, "Low Match")]

YEARS_REQ = re.compile(
    r"(\d{1,2})\s*(?:\+|plus)?\s*(?:-|–|to)?\s*(?:\d{1,2})?\s*\+?\s*(?:years?|yrs?)\b(?:[^.\n]{0,40})(?:experience|exp\b)",
    re.I,
)
NICE_TO_HAVE = re.compile(r"(nice to have|preferred|a plus|is a bonus|bonus|desirable|advantage|optional|ideal(ly)?|strong signal)", re.I)
HAS_ADV_DEGREE = re.compile(r"\b(master|m\.?\s?a\b|m\.?\s?sc|mjmc|mba|m\.?\s?phil|ph\.?\s?d|doctorate)", re.I)
LANG_REQ = re.compile(
    r"(?:native|fluent|fluency|proficien\w*|mother[- ]tongue|bilingual|c2|c1)\s+(?:level\s+)?(?:in\s+|speaker of\s+)?(?:\w+\s+(?:and|&|or)\s+)?({langs})"
    r"|({langs})\s*(?:\(\w+\))?\s*(?:native|speaker|fluency|linguist|translator|language (?:specialist|expert))",
    re.I,
)
ASSESS_PAT = re.compile(r"(assessment|qualification (test|exam)|skills? test|sample translation|test task|onboarding test)", re.I)


def _lang_req_re() -> re.Pattern:
    langs = "|".join(T.LANGUAGES)
    return re.compile(LANG_REQ.pattern.format(langs=langs), re.I)


_LANG_REQ_RE = _lang_req_re()
_LEVELS = ["entry", "mid", "senior", "lead"]


def label_for(score: float) -> str:
    for threshold, label in LABEL_THRESHOLDS:
        if score >= threshold:
            return label
    return "Low Match"


@dataclass
class _Sim:
    values: list[float]


class Matcher:
    def __init__(
        self,
        profile: Profile,
        resume_text: str,
        weights: dict[str, float] | None = None,
        filters: SearchFilters | None = None,
        use_embeddings: bool = False,
    ):
        self.p = profile
        self.resume_text = resume_text or ""
        self.weights = {**DEFAULT_WEIGHTS, **(weights or {})}
        self.filters = filters or SearchFilters()
        self.use_embeddings = use_embeddings
        self.skills = set(profile.skills)
        self.tools = set(profile.tools)
        self.domains = set(profile.industries)
        self.languages = {l["language"].lower() for l in profile.languages}
        kw_blob = " ".join(profile.keywords + profile.skills + profile.tools + profile.certifications).lower()
        self.profile_blob = kw_blob + " " + self.resume_text.lower()
        self.years = profile.total_years_experience
        self.titles = [t for t in (profile.job_titles + profile.target_roles) if t]
        self.level = self._profile_level()

    # ---------------------------------------------------------------- helpers
    def _profile_level(self) -> str | None:
        y = self.years
        if y is None:
            return None
        if y >= 8 and ("Team lead / reviewer lead" in self.skills):
            return "lead"
        if y >= 5:
            return "senior"
        if y >= 2:
            return "mid"
        return "entry"

    def _similarities(self, docs: list[str]) -> list[float]:
        if not docs:
            return []
        corpus = [self.resume_text or self.profile_blob] + docs
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.metrics.pairwise import cosine_similarity
        except ImportError:  # e.g. no wheel for this platform: pure-Python fallback
            sims = _tfidf_sims(corpus)
        else:
            vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), min_df=1, max_features=40000, sublinear_tf=True)
            try:
                m = vec.fit_transform(corpus)
                sims = cosine_similarity(m[0:1], m[1:]).ravel().tolist()
            except ValueError:
                sims = [0.0] * len(docs)
        if self.use_embeddings:
            emb = _embedding_sims(corpus[0], docs)
            if emb:
                sims = [0.5 * a + 0.5 * b for a, b in zip(sims, emb)]
        return sims

    # ------------------------------------------------------------------ main
    def score_many(self, jobs: list[JobRecord]) -> list[dict[str, Any]]:
        docs = [f"{j.title}. {j.title}. {' '.join(j.tags)}. {j.description[:6000]}" for j in jobs]
        sims = self._similarities(docs)
        return [self.score(j, s) for j, s in zip(jobs, sims)]

    def score(self, job: JobRecord, similarity: float | None = None) -> dict[str, Any]:
        if similarity is None:
            similarity = self._similarities([f"{job.title}. {job.description[:6000]}"])[0]
        text = f"{job.title}\n{' '.join(job.tags)}\n{job.description}"
        low = text.lower()
        title_low = job.title.lower()
        w = self.weights
        comps: dict[str, dict[str, Any]] = {}
        reasons: list[str] = []
        missing: list[str] = []
        uncertain: list[str] = []
        hard: list[str] = []

        # ---------- languages
        job_langs = T.find_languages(text)
        hindi_job = "Hindi" in job_langs or "devanagari" in low
        required_langs, from_title = self._required_languages(job, text)
        has_hindi = "hindi" in self.languages
        lang_ok = True
        if required_langs:
            have = [l for l in required_langs if l.lower() in self.languages]
            if not have:
                if hindi_job and has_hindi and not from_title:
                    # Multi-language posting that also lists Hindi: any-of semantics
                    uncertain.append(f"Posting also asks for {', '.join(required_langs)} — check which languages are open")
                else:
                    lang_ok = False
                    hard.append(f"Requires {', '.join(required_langs)} — not on your profile")
        if hindi_job and has_hindi:
            reasons.append("Hindi is part of the role and is on your profile")

        # ---------- skills
        job_skills = set(T.find(text, "skills"))
        title_skills = set(T.find(job.title, "skills"))
        core_job = job_skills & T.CORE_SKILLS
        matched = sorted(job_skills & self.skills)
        unmatched_job_skills = sorted(job_skills - self.skills)
        job_tools = set(T.find(text, "tools"))
        matched_tools = sorted(job_tools & self.tools)
        related_hits = {s for s in job_skills - self.skills if RELATED.get(s, set()) & self.skills}
        if job_skills:
            coverage = (len(job_skills & self.skills) + 0.5 * len(related_hits)) / len(job_skills)
            # weight title-level skills double
            if title_skills:
                coverage = 0.6 * coverage + 0.4 * (len(title_skills & self.skills) / len(title_skills))
        else:
            coverage = min(1.0, similarity * 2.5)
        other_req = T.find(text, "other")
        for req in other_req:
            pat = T.compiled()["other"][req]
            if pat.search(self.profile_blob):
                continue
            if req.startswith("Advanced degree") and any(HAS_ADV_DEGREE.search(e.get("text", "")) for e in self.p.education):
                continue
            ctx_hits = [m for m in pat.finditer(text)]
            optional = all(NICE_TO_HAVE.search(text[max(0, m.start() - 120): m.end() + 120]) for m in ctx_hits)
            (uncertain if optional else missing).append(req)
        skill_factor = coverage * (0.75 ** len(missing)) * (0.92 ** len(uncertain))
        if matched_tools:
            skill_factor = min(1.0, skill_factor + 0.05 * len(matched_tools))
        comps["skills"] = {"value": skill_factor, "note": f"{len(matched)} of {len(job_skills) or '?'} detected skills match"}
        if matched:
            reasons.append("Your skills match: " + ", ".join(matched[:6]))
        for s in unmatched_job_skills:
            if s in related_hits:
                uncertain.append(f"{s} — close to your {', '.join(sorted(RELATED[s] & self.skills)[:2])} (minor upskilling)")
            elif s in T.CORE_SKILLS:
                uncertain.append(f"{s} (mentioned; not explicit on your profile)")

        # ---------- experience
        req_years = self._required_years(text)
        if req_years is None:
            yrs_factor, yrs_note = 0.75, "No minimum stated"
        elif self.years is None:
            yrs_factor, yrs_note = 0.6, f"Asks {req_years}+ yrs; your total is unknown"
            uncertain.append(f"{req_years}+ years experience (set your years in Profile)")
        elif self.years >= req_years:
            yrs_factor, yrs_note = 1.0, f"Asks {req_years}+ yrs; you have {self.years:g}"
            reasons.append(f"Your {self.years:g} years meets the {req_years}+ years asked")
        else:
            gap = req_years - self.years
            yrs_factor = max(0.1, 1 - 0.2 * gap)
            yrs_note = f"Asks {req_years}+ yrs; you have {self.years:g}"
            (missing if gap > 2 else uncertain).append(f"{req_years}+ years experience")
        title_rel = max((fuzz.token_set_ratio(title_low, t.lower()) for t in self.titles), default=0) / 100
        exp_factor = 0.65 * yrs_factor + 0.35 * title_rel
        comps["experience"] = {"value": exp_factor, "note": yrs_note}

        # ---------- responsibilities (text similarity)
        resp_factor = min(1.0, (max(similarity, 0.0) / 0.18) ** 0.7)
        comps["responsibilities"] = {"value": resp_factor, "note": f"Text similarity {similarity:.2f}"}

        # ---------- language & domain
        job_domains = set(T.find(text, "domains"))
        dom_overlap = job_domains & self.domains
        ld = 0.0
        if hindi_job and has_hindi:
            ld += 0.6
        elif not required_langs and core_job:
            ld += 0.35  # language-neutral role in your field
        if "English" in job_langs and "english" in self.languages:
            ld += 0.1
        if job_domains:
            ld += 0.3 * (len(dom_overlap) / len(job_domains))
        else:
            ld += 0.15
        comps["language_domain"] = {"value": min(1.0, ld), "note": ", ".join(sorted(dom_overlap)) or "No domain overlap detected"}

        # ---------- location / work arrangement
        loc_factor, loc_note, loc_hard = self._location(job, text)
        if loc_hard:
            hard.append(loc_note)
        comps["location"] = {"value": loc_factor, "note": loc_note}
        tc = TITLE_COUNTRY.search(job.title)
        if tc and "India" not in (job.eligible_regions or []) and not re.search(r"\bindia\b", job.title, re.I):
            hard.append(f"Role is for people based in {tc.group(1).title()}")
        for name, pat in T.HARD_INELIGIBLE_PATTERNS.items():
            if re.search(pat, low):
                hard.append(name)

        # ---------- seniority
        jl = self._job_level(job)
        if not jl or not self.level:
            sen, sen_note = 0.6, "Level unclear"
        else:
            d = _LEVELS.index(jl) - _LEVELS.index(self.level)
            # Under-qualified is penalised more than over-qualified
            sen = [1.0, 0.55, 0.25, 0.1][d] if d >= 0 else [1.0, 0.8, 0.6, 0.45][-d]
            sen_note = f"Role looks {jl}; you look {self.level}"
        comps["seniority"] = {"value": sen, "note": sen_note}

        # ---------- compensation
        annual = N.annual_usd(job.salary_max or job.salary_min, job.salary_currency, job.salary_period)
        if annual is None:
            comp, comp_note = 0.5, "Salary not disclosed"
        elif self.filters.min_salary:
            comp = 1.0 if annual >= self.filters.min_salary else 0.2
            comp_note = f"≈ ${annual:,.0f}/yr (approx.)"
        else:
            comp, comp_note = 0.8, f"≈ ${annual:,.0f}/yr (approx.)"
        comps["compensation"] = {"value": comp, "note": comp_note}

        # ---------- qualifications
        q = 0.3
        if matched_tools:
            q += 0.3
            reasons.append("Tools you know: " + ", ".join(matched_tools))
        if re.search(r"journalism|mass comm|linguistics|hindi literature|communications? degree", low):
            if any(e.get("field_mass_comm") for e in self.p.education) or re.search(r"linguistic|journalism|mass comm", self.profile_blob):
                q += 0.4
                reasons.append("Your journalism / language education is relevant")
            else:
                uncertain.append("Education in journalism/linguistics mentioned")
        elif self.p.certifications:
            q += 0.15
        comps["qualifications"] = {"value": min(1.0, q), "note": ", ".join(matched_tools) or "—"}

        # ---------- total
        total_w = sum(w.values()) or 100
        score = sum(comps[k]["value"] * w[k] for k in comps) * 100 / total_w
        for k in comps:
            comps[k]["points"] = round(comps[k]["value"] * w[k] * 100 / total_w, 1)
            comps[k]["max"] = round(w[k] * 100 / total_w, 1)
            comps[k]["label"] = COMPONENT_LABELS[k]
            del comps[k]["value"]

        # Relevance gate — title overlap alone never earns a high score
        in_scope = hindi_job or len(core_job) >= 2 or bool(title_skills & T.CORE_SKILLS)
        if not in_scope:
            score = min(score, 35)
            reasons.insert(0, "Not clearly a language / AI-data role — capped as Low Match")
        elif not hindi_job and not (title_skills & (T.CORE_SKILLS - {"LLM / AI output evaluation", "Quality control"})):
            # e.g. "Financial Task Design and Evaluation": AI-data flavoured, but not a language job
            score = min(score, 62)
        caution = [name for name, pat in CAUTION_PATTERNS.items() if pat.search(text[:8000])]
        if caution:
            score = min(score, 40)
            reasons.insert(0, "Be careful: " + "; ".join(caution).lower())
        if not lang_ok or hard:
            label = "Not Eligible"
            score = min(score, 25)
        else:
            label = label_for(score)

        n_missing = len(missing)
        upskilling = "None" if n_missing == 0 and len(uncertain) <= 1 else ("Minor" if n_missing <= 1 else "Significant")
        if not in_scope:
            upskilling = "Significant"
        desc_len = len(job.description or "")
        known = sum(bool(x) for x in (job.location, job.company, job.posted_at, job.job_type != "unknown"))
        confidence = "High" if desc_len >= 800 and known >= 3 else "Medium" if desc_len >= 200 else "Low"
        effort = self._effort(job, text)
        return {
            "score": round(score, 1),
            "label": label,
            "components": comps,
            "reasons": reasons[:8] or ["Partial overlap with your profile"],
            "matched_skills": matched + [f"Tool: {t}" for t in matched_tools],
            "missing": _uniq(missing),
            "uncertain": _uniq(uncertain)[:8],
            "hard_blockers": _uniq(hard),
            "required_languages": required_langs,
            "hindi": hindi_job,
            "upskilling": upskilling,
            "eligibility": {"ok": not hard and lang_ok, "note": loc_note},
            "effort": effort,
            "confidence": confidence,
            "in_scope": in_scope,
            "caution": caution,
            "categories": derive_target_roles(Profile(
                skills=sorted(job_skills), languages=[{"language": "Hindi", "level": ""}] if hindi_job else []
            )),
        }

    # -------------------------------------------------------------- pieces
    def _required_languages(self, job: JobRecord, text: str) -> tuple[list[str], bool]:
        """Returns (languages, came_from_title). Title languages are any-of
        ("Hindi / Tamil linguist" is satisfiable with Hindi)."""
        req: list[str] = []
        title_langs = [l for l in T.find_languages(job.title) if l != "English"]
        if title_langs:
            return title_langs, True
        for m in _LANG_REQ_RE.finditer(text[:5000]):
            lang = (m.group(1) or m.group(2) or "").title()
            lang = T.LANGUAGE_ALIASES.get(lang.lower(), lang)
            if lang and lang != "English" and lang not in req:
                req.append(lang)
        return req, False

    def _required_years(self, text: str) -> int | None:
        vals = []
        for m in YEARS_REQ.finditer(text[:8000]):
            try:
                n = int(m.group(1))
            except ValueError:
                continue
            if 0 < n <= 25:
                ctx = text[max(0, m.start() - 80): m.end() + 40]
                if not NICE_TO_HAVE.search(ctx):
                    vals.append(n)
        return min(vals) if vals else None

    def _location(self, job: JobRecord, text: str) -> tuple[float, str, bool]:
        regions = set(job.eligible_regions or [])
        modes = set(self.filters.work_modes or ["remote", "hybrid", "onsite"])
        mode = job.remote_type
        mode_pen = 1.0 if (mode in modes or mode == "unknown") else 0.4
        if "India" in regions:
            return 1.0 * mode_pen, f"{mode.title()} · open to India", False
        if "Worldwide" in regions and mode == "remote":
            return 1.0 * mode_pen, "Remote · worldwide", False
        if mode == "remote":
            if regions - {"Worldwide"}:
                return 0.0, f"Remote but restricted to {', '.join(sorted(regions - {'Worldwide'}))}", True
            return 0.65 * mode_pen, "Remote · eligible regions not stated (check posting)", False
        if mode in ("onsite", "hybrid"):
            if regions & {"USA", "Europe", "LATAM"}:
                return 0.0, f"{mode.title()} in {', '.join(sorted(regions))} — outside India", True
            return 0.5 * mode_pen, f"{mode.title()} · location unclear: {job.location or 'n/a'}", False
        if regions & {"USA", "Europe", "LATAM"} and not regions & {"India", "Worldwide"}:
            return 0.15, f"Located in {', '.join(sorted(regions))} — remote status unclear", False
        return 0.5, f"Work arrangement unclear ({job.location or 'no location'})", False

    def _job_level(self, job: JobRecord) -> str | None:
        s = (job.seniority or "").lower()
        if any(x in s for x in ("lead", "principal", "director", "manager", "head")):
            return "lead"
        if "senior" in s or "sr" == s or "staff" in s:
            return "senior"
        if any(x in s for x in ("entry", "junior", "intern", "graduate")):
            return "entry"
        if s in ("mid", "mid-level", "middle", "mid_level", "mid-senior"):
            return "mid"
        return None if not s else "mid"

    def _effort(self, job: JobRecord, text: str) -> str:
        url = (job.apply_url or job.url or "").lower()
        level = 1
        if any(h in url for h in ("greenhouse", "lever.co", "ashbyhq", "workable", "smartrecruiters")):
            level = 2
        if ASSESS_PAT.search(text):
            level += 1
        if re.search(r"cover letter", text, re.I):
            level += 1
        return ["Low (quick apply)", "Low–Medium (short form)", "Medium (ATS form ~15–20 min)", "High (form + test/cover letter)", "High (form + test + cover letter)"][min(level, 4)]


_STOP = set("a an and are as at be by for from has have in is it its of on or our the this to we will with you your".split())


def _tfidf_sims(corpus: list[str]) -> list[float]:
    import math
    from collections import Counter

    def toks(t: str) -> list[str]:
        w = [x for x in re.findall(r"[a-z\u0900-\u097f]{2,}", t.lower()) if x not in _STOP]
        return w + [f"{a} {b}" for a, b in zip(w, w[1:])]

    counts = [Counter(toks(d)) for d in corpus]
    df = Counter(t for c in counts for t in c)
    n = len(corpus)
    vecs = []
    for c in counts:
        v = {t: (1 + math.log(f)) * (math.log((1 + n) / (1 + df[t])) + 1) for t, f in c.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        vecs.append({t: x / norm for t, x in v.items()})
    q = vecs[0]
    return [sum(q.get(t, 0) * x for t, x in d.items()) for d in vecs[1:]]


def _uniq(items: list[str]) -> list[str]:
    seen, out = set(), []
    for i in items:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


_EMB_MODEL = None


def _embedding_sims(query: str, docs: list[str]) -> list[float] | None:
    """Optional local embeddings (sentence-transformers). Returns None if unavailable."""
    global _EMB_MODEL
    try:
        from sentence_transformers import SentenceTransformer, util  # type: ignore
    except ImportError:
        return None
    try:
        if _EMB_MODEL is None:
            _EMB_MODEL = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
        q = _EMB_MODEL.encode([query[:4000]], convert_to_tensor=True)
        d = _EMB_MODEL.encode([x[:2000] for x in docs], convert_to_tensor=True, batch_size=32)
        return [float(max(0.0, v)) * 0.6 for v in util.cos_sim(q, d)[0].tolist()]
    except Exception:
        return None


def quick_relevance(job: JobRecord) -> bool:
    """Cheap pre-filter for bulk feeds: keep anything with a language/AI-data signal."""
    text = f"{job.title} {' '.join(job.tags)} {job.description[:3000]}"
    if re.search(r"\bhindi\b|devanagari|\bindic\b|multilingual|bilingual", text, re.I):
        return True
    title_core = set(T.find(job.title, "skills")) & T.CORE_SKILLS
    if title_core:
        return True
    core = set(T.find(text, "skills")) & T.CORE_SKILLS
    return len(core) >= 2
