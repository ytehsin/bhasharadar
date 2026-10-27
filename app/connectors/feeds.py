"""RSS/Atom feeds and user-supplied company career pages.

Career pages are read only when robots.txt allows it, URLs are SSRF-checked,
and only schema.org ``JobPosting`` structured data (JSON-LD) is extracted —
the same data sites publish for search engines. If a page embeds a known ATS
board (Greenhouse/Lever/Ashby/Workable) we report it so you can add the board
connector instead, which is more reliable.
"""
from __future__ import annotations

import json
import re
from typing import Any

import feedparser
from bs4 import BeautifulSoup

from .. import job_normalizer as N
from ..http_client import FetchError
from ..models import JobRecord
from ..security import UnsafeURLError, validate_public_url
from .base import BaseConnector, SearchContext, SourceMeta


class RSSConnector(BaseConnector):
    meta = SourceMeta(
        kind="rss",
        name="RSS / Atom feeds",
        source_type="RSS feed",
        docs_url="https://en.wikipedia.org/wiki/RSS",
        endpoint="(list of feed URLs in config)",
        rate_limit="App: 2s spacing per host, cached 3h",
        coverage="Whatever feeds you add",
        filters="none (filtered locally)",
        terms="Use feeds published for syndication; link back to each item.",
        min_interval_s=2,
        cache_ttl_s=3 * 3600,
        verification="Generic parser (feedparser). Default feed: We Work Remotely public RSS — "
        "confirm with Health check",
        attribution="Job via RSS feed",
    )

    def feeds(self) -> list[dict[str, str]]:
        out = []
        for f in self.config.get("feeds", []):
            if isinstance(f, str):
                f = {"url": f, "name": f}
            if f.get("url"):
                out.append({"url": f["url"], "name": f.get("name") or f["url"]})
        return out

    def is_configured(self) -> tuple[bool, str]:
        return (True, "") if self.feeds() else (False, "No feeds added yet")

    async def search(self, ctx: SearchContext) -> list[JobRecord]:
        out: list[JobRecord] = []
        errors = []
        for feed in self.feeds():
            if ctx.cancelled:
                break
            try:
                validate_public_url(feed["url"], resolve=False)
                text = await ctx.http.get_text(feed["url"], cache_ttl=self.meta.cache_ttl_s)
            except (FetchError, UnsafeURLError) as exc:
                errors.append(f"{feed['name']}: {exc}")
                continue
            parsed = feedparser.parse(text)
            for entry in parsed.entries[:300]:
                entry["_feed"] = feed["name"]
                job = self.safe_normalize(entry)
                if job:
                    out.append(job)
        if errors and not out and len(errors) == len(self.feeds()):
            raise FetchError("; ".join(errors))
        return out

    def normalize(self, e: Any) -> JobRecord:
        title = e.get("title", "")
        company = None
        # Common "Company: Role" pattern (We Work Remotely and others)
        m = re.match(r"^\s*([^:]{2,60}):\s+(.+)$", title)
        if m:
            company, title = m.group(1), m.group(2)
        region = e.get("region") or e.get("location")
        return N.build(
            source_id=self.source_id,
            source_name=f"{self.name} · {e['_feed']}",
            source_job_id=e.get("id") or e.get("link"),
            title=title,
            company=company or e.get("author"),
            location=region,
            description=N.html_to_text(e.get("summary") or e.get("description")),
            url=e.get("link"),
            apply_url=e.get("link"),
            posted_at=N.to_iso(e.get("published") or e.get("updated")),
            tags=[t.get("term") for t in e.get("tags", []) if t.get("term")],
        )

    async def health_check(self, http) -> tuple[bool, str]:
        if not self.feeds():
            return False, "No feeds configured"
        from ..models import SearchFilters

        jobs = await self.search(SearchContext(http=http, queries=[], filters=SearchFilters()))
        return True, f"OK — {len(jobs)} item(s) across {len(self.feeds())} feed(s)"


ATS_PATTERNS = {
    "greenhouse": re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)"),
    "lever": re.compile(r"jobs(?:\.eu)?\.lever\.co/([A-Za-z0-9_.-]+)"),
    "ashby": re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)"),
    "workable": re.compile(r"apply\.workable\.com/([A-Za-z0-9_-]+)"),
}


def detect_ats(html_text: str) -> dict[str, str]:
    found = {}
    for kind, pat in ATS_PATTERNS.items():
        m = pat.search(html_text)
        if m and m.group(1).lower() not in {"embed", "api", "j"}:
            found[kind] = m.group(1)
    return found


def extract_jobpostings(html_text: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html_text, "html.parser")
    postings: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for n in node:
                walk(n)
        elif isinstance(node, dict):
            t = node.get("@type")
            types = t if isinstance(t, list) else [t]
            if "JobPosting" in types:
                postings.append(node)
            for key in ("@graph", "itemListElement", "item"):
                if key in node:
                    walk(node[key])

    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            walk(json.loads(tag.string or tag.get_text() or "null"))
        except (json.JSONDecodeError, TypeError):
            continue
    return postings


class CareerPageConnector(BaseConnector):
    meta = SourceMeta(
        kind="careers",
        name="Company career pages",
        source_type="Career page",
        docs_url="https://schema.org/JobPosting",
        endpoint="(list of career page URLs in config)",
        rate_limit="App: 3s spacing per host, robots.txt respected, cached 6h",
        coverage="Pages you add",
        filters="none (filtered locally)",
        terms="Reads only public pages allowed by robots.txt; extracts schema.org JobPosting data.",
        min_interval_s=3,
        cache_ttl_s=6 * 3600,
        verification="Generic extractor; each URL is SSRF-validated and robots-checked at run time",
        attribution="Company careers page",
    )

    def pages(self) -> list[dict[str, str]]:
        out = []
        for p in self.config.get("pages", []):
            if isinstance(p, str):
                p = {"url": p}
            if p.get("url"):
                out.append({"url": p["url"], "company": p.get("company", "")})
        return out

    def is_configured(self) -> tuple[bool, str]:
        return (True, "") if self.pages() else (False, "No career pages added yet")

    async def search(self, ctx: SearchContext) -> list[JobRecord]:
        out: list[JobRecord] = []
        self.ats_hints: dict[str, dict[str, str]] = {}
        errors = []
        for page in self.pages():
            if ctx.cancelled:
                break
            try:
                url = validate_public_url(page["url"])
                html_text = await ctx.http.get_text(url, check_robots=True, cache_ttl=self.meta.cache_ttl_s)
            except (FetchError, UnsafeURLError) as exc:
                errors.append(f"{page['url']}: {exc}")
                continue
            hints = detect_ats(html_text)
            if hints:
                self.ats_hints[page["url"]] = hints
            for raw in extract_jobpostings(html_text):
                raw["_page"] = page
                job = self.safe_normalize(raw)
                if job:
                    out.append(job)
        if errors and not out and len(errors) == len(self.pages()):
            raise FetchError("; ".join(errors))
        return out

    def normalize(self, p: dict[str, Any]) -> JobRecord:
        org = p.get("hiringOrganization") or {}
        org_name = org.get("name") if isinstance(org, dict) else str(org)
        locs = p.get("jobLocation") or []
        locs = locs if isinstance(locs, list) else [locs]
        loc_names = []
        for l in locs:
            addr = (l or {}).get("address") or {}
            if isinstance(addr, dict):
                loc_names.append(", ".join(filter(None, [addr.get("addressLocality"), addr.get("addressCountry") if isinstance(addr.get("addressCountry"), str) else (addr.get("addressCountry") or {}).get("name")])))
        tele = str(p.get("jobLocationType", "")).upper() == "TELECOMMUTE"
        sal = p.get("baseSalary") or {}
        val = sal.get("value") if isinstance(sal, dict) else {}
        val = val if isinstance(val, dict) else {"value": val}
        emp = p.get("employmentType")
        emp = " ".join(emp) if isinstance(emp, list) else emp
        url = p.get("url") or p["_page"]["url"]
        return N.build(
            source_id=self.source_id,
            source_name=self.name,
            source_job_id=str((p.get("identifier") or {}).get("value") if isinstance(p.get("identifier"), dict) else p.get("identifier") or url),
            title=p.get("title"),
            company=org_name or p["_page"].get("company"),
            location=("Remote; " if tele else "") + "; ".join(n for n in loc_names if n),
            remote_type="remote" if tele else "unknown",
            job_type_hint=emp,
            description=N.html_to_text(p.get("description")),
            url=url,
            apply_url=url,
            posted_at=N.to_iso(p.get("datePosted")),
            deadline=N.to_iso(p.get("validThrough")),
            salary_min=val.get("minValue") or val.get("value"),
            salary_max=val.get("maxValue"),
            salary_currency=sal.get("currency") if isinstance(sal, dict) else None,
            salary_period=(str(val.get("unitText") or "").lower() or None),
        )

    async def health_check(self, http) -> tuple[bool, str]:
        if not self.pages():
            return False, "No career pages configured"
        from ..models import SearchFilters

        jobs = await self.search(SearchContext(http=http, queries=[], filters=SearchFilters()))
        hints = getattr(self, "ats_hints", {})
        msg = f"OK — {len(jobs)} JobPosting item(s)"
        if hints:
            msg += f"; ATS boards detected: {hints}"
        return True, msg
