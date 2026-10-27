"""Builds search queries from the profile, chosen categories and past strong matches."""
from __future__ import annotations

import re

from . import db
from .models import Profile, SearchFilters
from .taxonomy import CATEGORY_QUERIES

BASE_QUERIES = ["Hindi", "Hindi linguist", "Hindi translator", "Hindi AI trainer", "localization"]


def _norm_title(title: str) -> str:
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", title)
    t = re.sub(r"[-–—|:,].*$", "", t)
    t = re.sub(r"\b(freelance|remote|part[- ]time|full[- ]time|contract|india|senior|junior|sr\.?|jr\.?)\b", " ", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip()


def learned_titles(limit: int = 6) -> list[str]:
    """Automatically expand searches with titles of jobs that scored well before."""
    rows = db.query(
        "SELECT title FROM jobs WHERE match_score >= 70 AND match_label != 'Not Eligible' ORDER BY match_score DESC LIMIT 60"
    )
    seen, out = set(), []
    for r in rows:
        t = _norm_title(r["title"])
        k = t.lower()
        if 4 <= len(t) <= 50 and k not in seen:
            seen.add(k)
            out.append(t)
        if len(out) >= limit:
            break
    return out


def generate(profile: Profile, filters: SearchFilters) -> list[str]:
    queries: list[str] = []
    if filters.keywords.strip():
        queries += [k.strip() for k in re.split(r"[,;\n]", filters.keywords) if k.strip()]
    cats = filters.categories or profile.target_roles or list(CATEGORY_QUERIES)[:10]
    # Round-robin across categories so each gets its best query first
    buckets = [CATEGORY_QUERIES.get(c, [c]) for c in cats]
    depth = max((len(b) for b in buckets), default=0)
    for i in range(depth):
        for b in buckets:
            if i < len(b):
                queries.append(b[i])
    if not filters.categories:
        queries = BASE_QUERIES + queries
        queries += learned_titles()
    seen, out = set(), []
    for q in queries:
        k = q.lower()
        if k not in seen:
            seen.add(k)
            out.append(q)
    return out[: max(3, filters.max_queries)]
