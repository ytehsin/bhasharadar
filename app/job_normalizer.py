"""Helpers that turn messy source data into the common JobRecord format."""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from bs4 import BeautifulSoup

from .models import JobRecord

# ---------------------------------------------------------------- text ----

def html_to_text(value: str | None) -> str:
    if not value:
        return ""
    text = html.unescape(str(value))  # Greenhouse double-escapes HTML
    if "<" in text and ">" in text:
        soup = BeautifulSoup(text, "html.parser")
        for br in soup.find_all(["br", "p", "li", "div", "h1", "h2", "h3", "h4", "tr"]):
            br.insert_before("\n")
        text = soup.get_text(" ")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def clean(value: Any) -> str | None:
    if value is None:
        return None
    s = re.sub(r"\s+", " ", str(value)).strip()
    return s or None


# --------------------------------------------------------------- dates ----

def to_iso(value: Any) -> str | None:
    """Accepts ISO strings, RFC-822 strings, epoch seconds or milliseconds."""
    if value in (None, "", 0):
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            num = float(value)
            if num > 1e12:  # milliseconds
                num /= 1000
            return datetime.fromtimestamp(num, tz=timezone.utc).replace(microsecond=0).isoformat()
        s = str(value).strip()
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            try:
                dt = parsedate_to_datetime(s)
            except (TypeError, ValueError):
                m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
                if not m:
                    return None
                dt = datetime.fromisoformat(m.group(1))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def age_days(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 86400
    except ValueError:
        return None


# ------------------------------------------------------ location / remote ----

WORLDWIDE_PAT = re.compile(r"\b(worldwide|anywhere|global(ly)?|any location|all countries|remote[- ]first, anywhere)\b", re.I)
INDIA_PAT = re.compile(
    r"\b(india|indian|bharat|delhi|new delhi|ncr|noida|gurgaon|gurugram|ghaziabad|mumbai|bombay|bengaluru|bangalore|"
    r"hyderabad|chennai|pune|kolkata|ahmedabad|jaipur|lucknow|chandigarh|kochi|indore|bhopal|patna|apac|asia)\b",
    re.I,
)
REGION_PATTERNS: dict[str, re.Pattern] = {
    "USA": re.compile(r"\b(usa|u\.s\.a?\.?|united states|us only|us-based|north america|americas|canada)\b", re.I),
    "Europe": re.compile(r"\b(europe|eu|emea|uk|united kingdom|germany|france|spain|netherlands|poland|ireland)\b", re.I),
    "LATAM": re.compile(r"\b(latam|latin america|brazil|mexico|argentina|colombia)\b", re.I),
}
HYBRID_PAT = re.compile(r"\bhybrid\b", re.I)
REMOTE_PAT = re.compile(r"\b(remote|work from home|wfh|telecommut\w*|distributed|home[- ]based)\b", re.I)
ONSITE_PAT = re.compile(r"\b(on[- ]?site|in[- ]office|office[- ]based|in person)\b", re.I)


def classify_remote(*texts: str | None, explicit: str | bool | None = None) -> str:
    if isinstance(explicit, bool):
        return "remote" if explicit else "unknown"
    if isinstance(explicit, str):
        e = explicit.lower()
        if "hybrid" in e:
            return "hybrid"
        if "remote" in e:
            return "remote"
        if "site" in e or "office" in e:
            return "onsite"
    blob = " ".join(t for t in texts if t)
    if HYBRID_PAT.search(blob):
        return "hybrid"
    if REMOTE_PAT.search(blob):
        return "remote"
    if ONSITE_PAT.search(blob):
        return "onsite"
    return "unknown"


def classify_regions(*texts: str | None) -> list[str]:
    blob = " ".join(t for t in texts if t)
    regions: list[str] = []
    if WORLDWIDE_PAT.search(blob):
        regions.append("Worldwide")
    if INDIA_PAT.search(blob):
        regions.append("India")
    for name, pat in REGION_PATTERNS.items():
        if pat.search(blob):
            regions.append(name)
    return regions


# --------------------------------------------------------------- job type ----

def classify_job_type(*texts: str | None) -> str:
    blob = " ".join(t for t in texts if t).lower()
    if not blob:
        return "unknown"
    if re.search(r"\bfreelanc", blob):
        return "freelance"
    if re.search(r"\b(contract|contractor|fixed[- ]term|project[- ]based|consultant)\b", blob):
        return "contract"
    if re.search(r"\bintern(ship)?\b", blob):
        return "internship"
    if re.search(r"\b(part[- ]?time|parttime)\b", blob):
        return "part_time"
    if re.search(r"\b(temporary|temp)\b", blob):
        return "temporary"
    if re.search(r"\b(full[- ]?time|fulltime|permanent)\b", blob):
        return "full_time"
    return "unknown"


SENIORITY_PAT = [
    ("lead", re.compile(r"\b(lead|principal|head of|manager|director)\b", re.I)),
    ("senior", re.compile(r"\b(senior|sr\.?|staff)\b", re.I)),
    ("entry", re.compile(r"\b(junior|jr\.?|entry[- ]level|graduate|trainee|intern)\b", re.I)),
]


def classify_seniority(title: str, explicit: Any = None) -> str | None:
    if explicit:
        val = explicit[0] if isinstance(explicit, list) and explicit else explicit
        return clean(str(val)).lower() if val else None
    for name, pat in SENIORITY_PAT:
        if pat.search(title or ""):
            return name
    return "mid"


# ----------------------------------------------------------------- salary ----

_SAL_RANGE = re.compile(
    r"(?P<cur>[$€£₹]|usd|eur|gbp|inr|rs\.?)?\s*(?P<a>\d[\d,.]*)\s*(?P<ak>k)?\s*(?:-|–|to)\s*(?P<cur2>[$€£₹]|usd|eur|gbp|inr|rs\.?)?\s*(?P<b>\d[\d,.]*)\s*(?P<bk>k)?",
    re.I,
)
_CUR = {"$": "USD", "usd": "USD", "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP", "₹": "INR", "inr": "INR", "rs": "INR", "rs.": "INR"}


def parse_salary_text(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    m = _SAL_RANGE.search(text)
    if not m:
        return {"salary_text": clean(text)}

    def num(v: str, k: str | None) -> float | None:
        try:
            n = float(v.replace(",", ""))
        except ValueError:
            return None
        return n * 1000 if k else n

    cur = (m.group("cur") or m.group("cur2") or "").lower()
    period = None
    low = text.lower()
    if re.search(r"(/|per |an? )\s*(hr|hour)", low):
        period = "hour"
    elif re.search(r"(/|per |a )\s*(mo|month)", low):
        period = "month"
    elif re.search(r"(/|per |a )\s*(yr|year|annum)|annual", low):
        period = "year"
    return {
        "salary_min": num(m.group("a"), m.group("ak")),
        "salary_max": num(m.group("b"), m.group("bk") or m.group("ak")),
        "salary_currency": _CUR.get(cur),
        "salary_period": period,
        "salary_text": clean(text),
    }


# Approximate conversion to annual USD for comparison only (documented as such).
_FX_TO_USD = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27, "INR": 0.012, "CAD": 0.73, "AUD": 0.66}
_PERIOD_TO_YEAR = {"year": 1, "month": 12, "week": 52, "day": 260, "hour": 2080}


def annual_usd(amount: float | None, currency: str | None, period: str | None) -> float | None:
    if not amount:
        return None
    fx = _FX_TO_USD.get((currency or "USD").upper())
    if fx is None:
        return None
    mult = _PERIOD_TO_YEAR.get((period or "year").lower(), 1)
    return amount * fx * mult


# --------------------------------------------------------------- builder ----

def build(**kw: Any) -> JobRecord:
    """Build a JobRecord, filling derived fields when not given."""
    title = clean(html.unescape(str(kw.get("title") or ""))) or "Untitled role"
    desc = kw.get("description") or ""
    location = clean(kw.get("location"))
    kw["title"] = title
    if kw.get("company"):
        kw["company"] = clean(html.unescape(str(kw["company"])))
    kw["location"] = location
    if not kw.get("remote_type") or kw.get("remote_type") == "unknown":
        kw["remote_type"] = classify_remote(location, title, desc[:1500])
    if not kw.get("eligible_regions"):
        kw["eligible_regions"] = classify_regions(location)
        if kw["remote_type"] == "remote" and not kw["eligible_regions"]:
            # Look into the description for explicit restrictions
            kw["eligible_regions"] = classify_regions(desc[:3000])
    if kw["remote_type"] == "unknown" and "Worldwide" in kw["eligible_regions"]:
        kw["remote_type"] = "remote"
    if not kw.get("job_type") or kw.get("job_type") == "unknown":
        kw["job_type"] = classify_job_type(kw.get("job_type_hint"), title, desc[:2000])
    kw.pop("job_type_hint", None)
    if not kw.get("seniority"):
        kw["seniority"] = classify_seniority(title)
    kw["tags"] = [str(t).strip() for t in (kw.get("tags") or []) if str(t).strip()][:30]
    return JobRecord(**{k: v for k, v in kw.items() if k in JobRecord.model_fields})
