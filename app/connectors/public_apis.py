"""Free, keyless public job APIs."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus

from .. import job_normalizer as N
from ..models import JobRecord
from .base import BaseConnector, SearchContext, SourceMeta


# --------------------------------------------------------------- Remotive ----
class RemotiveConnector(BaseConnector):
    """Remotive asks for max ~4 calls/day and ≤2/min, so we make ONE bulk call
    per 6 hours (cached) and filter locally instead of one call per query."""

    meta = SourceMeta(
        kind="remotive",
        name="Remotive",
        source_type="Public API",
        docs_url="https://github.com/remotive-com/remote-jobs-api",
        endpoint="https://remotive.com/api/remote-jobs",
        rate_limit="Max ~4 requests/day, >2/min blocked (app: 1 cached bulk call per 6h)",
        coverage="Remote jobs worldwide (many with region restrictions)",
        filters="category, company_name, search, limit",
        terms="Must link back to the Remotive job URL and credit Remotive; listings delayed 24h; "
        "do not redistribute to other job sites.",
        min_interval_s=35,
        cache_ttl_s=6 * 3600,
        verification="Docs (GitHub README) reviewed 2026-10-27; live fetch pending on your machine",
        attribution="Job via Remotive",
    )

    async def search(self, ctx: SearchContext) -> list[JobRecord]:
        data = await ctx.http.get_json(self.meta.endpoint, cache_ttl=self.meta.cache_ttl_s)
        out = []
        for raw in data.get("jobs", [])[: ctx.max_items * 3]:
            job = self.safe_normalize(raw)
            if job:
                out.append(job)
        return out

    def normalize(self, raw: dict[str, Any]) -> JobRecord:
        sal = N.parse_salary_text(raw.get("salary"))
        loc = raw.get("candidate_required_location") or "Remote"
        return N.build(
            source_id=self.source_id,
            source_name=self.name,
            source_job_id=str(raw.get("id")),
            title=raw.get("title"),
            company=raw.get("company_name"),
            location=loc,
            remote_type="remote",
            eligible_regions=N.classify_regions(loc),
            job_type_hint=raw.get("job_type"),
            description=N.html_to_text(raw.get("description")),
            url=raw.get("url"),
            apply_url=raw.get("url"),
            posted_at=N.to_iso(raw.get("publication_date")),
            tags=[raw.get("category")] + list(raw.get("tags") or []),
            **sal,
        )


# -------------------------------------------------------------- Arbeitnow ----
class ArbeitnowConnector(BaseConnector):
    meta = SourceMeta(
        kind="arbeitnow",
        name="Arbeitnow",
        source_type="Public API",
        docs_url="https://arbeitnow.com/blog/job-board-api/",
        endpoint="https://www.arbeitnow.com/api/job-board-api",
        rate_limit="Not published (app: 2s spacing, ≤5 pages)",
        coverage="Mostly Europe (Germany); some remote",
        filters="page, visa_sponsorship",
        terms="Free, no API key. Link to original posting.",
        min_interval_s=2,
        cache_ttl_s=3 * 3600,
        verification="Docs page reviewed 2026-10-27",
        attribution="Job via Arbeitnow",
    )

    async def search(self, ctx: SearchContext) -> list[JobRecord]:
        out: list[JobRecord] = []
        max_pages = int(self.config.get("max_pages", 5))
        for page in range(1, max_pages + 1):
            if ctx.cancelled:
                break
            data = await ctx.http.get_json(self.meta.endpoint, params={"page": page}, cache_ttl=self.meta.cache_ttl_s)
            items = data.get("data", [])
            for raw in items:
                job = self.safe_normalize(raw)
                if job:
                    out.append(job)
            if not items or not (data.get("links") or {}).get("next"):
                break
        return out

    def normalize(self, raw: dict[str, Any]) -> JobRecord:
        remote = raw.get("remote")
        return N.build(
            source_id=self.source_id,
            source_name=self.name,
            source_job_id=raw.get("slug"),
            title=raw.get("title"),
            company=raw.get("company_name"),
            location=raw.get("location"),
            remote_type="remote" if remote is True else "unknown",
            job_type_hint=" ".join(raw.get("job_types") or []),
            description=N.html_to_text(raw.get("description")),
            url=raw.get("url"),
            apply_url=raw.get("url"),
            posted_at=N.to_iso(raw.get("created_at")),
            tags=raw.get("tags") or [],
        )


# ----------------------------------------------------------------- Jobicy ----
class JobicyConnector(BaseConnector):
    meta = SourceMeta(
        kind="jobicy",
        name="Jobicy",
        source_type="Public API",
        docs_url="https://github.com/Jobicy/remote-jobs-api",
        endpoint="https://jobicy.com/api/v2/remote-jobs",
        rate_limit="Poll at most once per hour; a few checks/day is enough (app: responses cached 3h)",
        coverage="Remote jobs worldwide; geo filter (e.g. apac, anywhere)",
        filters="count (1-200), geo, industry, tag (3-50 chars)",
        terms="Keep Jobicy as source and preserve its job URL; sanitize HTML; no spam/misleading databases.",
        min_interval_s=3,
        cache_ttl_s=3 * 3600,
        verification="Docs (GitHub README) reviewed 2026-10-27",
        attribution="Job via Jobicy",
    )

    async def search(self, ctx: SearchContext) -> list[JobRecord]:
        out: list[JobRecord] = []
        # One broad call + a handful of tag searches (tags are 3-50 chars).
        calls: list[dict[str, Any]] = [{"count": 100}]
        for q in ctx.queries[: int(self.config.get("max_tag_queries", 5))]:
            tag = q.strip()[:50]
            if len(tag) >= 3:
                calls.append({"count": 50, "tag": tag})
        for params in calls:
            if ctx.cancelled:
                break
            data = await ctx.http.get_json(self.meta.endpoint, params=params, cache_ttl=self.meta.cache_ttl_s)
            for raw in data.get("jobs", []) or []:
                job = self.safe_normalize(raw)
                if job:
                    out.append(job)
        return out

    def normalize(self, raw: dict[str, Any]) -> JobRecord:
        geo = raw.get("jobGeo") or "Remote"
        jt = raw.get("jobType")
        jt = " ".join(jt) if isinstance(jt, list) else jt
        ind = raw.get("jobIndustry")
        ind = ind if isinstance(ind, list) else [ind] if ind else []
        return N.build(
            source_id=self.source_id,
            source_name=self.name,
            source_job_id=str(raw.get("id")),
            title=N.html_to_text(raw.get("jobTitle")),
            company=raw.get("companyName"),
            location=geo,
            remote_type="remote",
            eligible_regions=N.classify_regions(geo),
            job_type_hint=jt,
            seniority=(raw.get("jobLevel") or None),
            description=N.html_to_text(raw.get("jobDescription") or raw.get("jobExcerpt")),
            url=raw.get("url"),
            apply_url=raw.get("url"),
            posted_at=N.to_iso(raw.get("pubDate")),
            salary_min=_num(raw.get("salaryMin")),
            salary_max=_num(raw.get("salaryMax")),
            salary_currency=raw.get("salaryCurrency") or None,
            salary_period=(raw.get("salaryPeriod") or None),
            tags=[N.html_to_text(i) for i in ind],
        )


# ---------------------------------------------------------------- RemoteOK ----
class RemoteOKConnector(BaseConnector):
    meta = SourceMeta(
        kind="remoteok",
        name="Remote OK",
        source_type="Public API",
        docs_url="https://remoteok.com/legal",
        endpoint="https://remoteok.com/api",
        rate_limit="Not published (app: 1 cached call per 6h)",
        coverage="Remote jobs (newest ~100), mostly tech",
        filters="none (single feed; filtered locally)",
        terms="Must hyperlink back to Remote OK where data is shown and name Remote OK as the source.",
        min_interval_s=10,
        cache_ttl_s=6 * 3600,
        verification="Terms page reviewed 2026-10-27",
        attribution="Job via Remote OK",
    )

    async def search(self, ctx: SearchContext) -> list[JobRecord]:
        data = await ctx.http.get_json(self.meta.endpoint, cache_ttl=self.meta.cache_ttl_s)
        out = []
        for raw in data if isinstance(data, list) else []:
            if not isinstance(raw, dict) or not raw.get("position"):
                continue  # first element is the legal notice
            job = self.safe_normalize(raw)
            if job:
                out.append(job)
        return out

    def normalize(self, raw: dict[str, Any]) -> JobRecord:
        loc = raw.get("location") or "Remote"
        return N.build(
            source_id=self.source_id,
            source_name=self.name,
            source_job_id=str(raw.get("id")),
            title=raw.get("position"),
            company=raw.get("company"),
            location=loc,
            remote_type="remote",
            eligible_regions=N.classify_regions(loc),
            description=N.html_to_text(raw.get("description")),
            url=raw.get("url"),
            apply_url=raw.get("apply_url") or raw.get("url"),
            posted_at=N.to_iso(raw.get("date") or raw.get("epoch")),
            salary_min=_num(raw.get("salary_min")) or None,
            salary_max=_num(raw.get("salary_max")) or None,
            salary_currency="USD" if _num(raw.get("salary_min")) else None,
            salary_period="year" if _num(raw.get("salary_min")) else None,
            tags=raw.get("tags") or [],
        )


# --------------------------------------------------------------- Himalayas ----
class HimalayasConnector(BaseConnector):
    meta = SourceMeta(
        kind="himalayas",
        name="Himalayas",
        source_type="Public API",
        docs_url="https://himalayas.app/docs/remote-jobs-api",
        endpoint="https://himalayas.app/jobs/api/search",
        rate_limit="Shared public allowance without key; data refreshes every 24h (app: cached 12h)",
        coverage="Remote jobs worldwide; country filter (e.g. India)",
        filters="q, country, worldwide, seniority, employment_type, page",
        terms="Show visible Himalayas credit with link; Apply must use the API's applicationLink; "
        "do not pass jobs to other boards/aggregators.",
        min_interval_s=2,
        cache_ttl_s=12 * 3600,
        verification="API reference reviewed 2026-10-27",
        attribution="Job via Himalayas (himalayas.app)",
    )

    async def search(self, ctx: SearchContext) -> list[JobRecord]:
        out: list[JobRecord] = []
        for q in ctx.queries[: int(self.config.get("max_queries", 6))]:
            for extra in ({"country": "India"}, {"worldwide": "true"}):
                if ctx.cancelled:
                    return out
                params = {"q": q, "page": 1, **extra}
                data = await ctx.http.get_json(self.meta.endpoint, params=params, cache_ttl=self.meta.cache_ttl_s)
                for raw in data.get("jobs", []) or []:
                    job = self.safe_normalize(raw)
                    if job:
                        out.append(job)
        return out

    def normalize(self, raw: dict[str, Any]) -> JobRecord:
        restr = raw.get("locationRestrictions") or []
        names = [r.get("name") if isinstance(r, dict) else str(r) for r in restr]
        loc = ", ".join(n for n in names if n) or "Worldwide"
        sen = raw.get("seniority")
        sen = sen[0] if isinstance(sen, list) and sen else sen
        return N.build(
            source_id=self.source_id,
            source_name=self.name,
            source_job_id=raw.get("guid"),
            title=raw.get("title"),
            company=raw.get("companyName"),
            location=f"Remote — {loc}",
            remote_type="remote",
            eligible_regions=["Worldwide"] if not names else N.classify_regions(loc),
            job_type_hint=raw.get("employmentType"),
            seniority=(str(sen).lower() if sen else None),
            description=N.html_to_text(raw.get("description") or raw.get("excerpt")),
            url=raw.get("applicationLink") or raw.get("guid"),
            apply_url=raw.get("applicationLink"),
            posted_at=N.to_iso(raw.get("pubDate")),
            deadline=N.to_iso(raw.get("expiryDate")),
            salary_min=_num(raw.get("minSalary")),
            salary_max=_num(raw.get("maxSalary")),
            salary_currency=raw.get("currency"),
            salary_period=(raw.get("salaryPeriod") or "year") if raw.get("minSalary") else None,
            tags=(raw.get("categories") or []) + (raw.get("parentCategories") or []),
        )


# ---------------------------------------------------------------- The Muse ----
class TheMuseConnector(BaseConnector):
    meta = SourceMeta(
        kind="themuse",
        name="The Muse",
        source_type="Public API",
        docs_url="https://www.themuse.com/developers/api/v2",
        endpoint="https://www.themuse.com/api/public/jobs",
        auth="None (optional api_key raises limits)",
        rate_limit="Documented per-hour limits; higher with free key",
        coverage="Mostly US; has 'Flexible / Remote' location",
        filters="page (required), category, level, location, company",
        terms="Free public API; link to the job's landing page.",
        min_interval_s=2,
        cache_ttl_s=6 * 3600,
        verification="API directory listing reviewed 2026-10-27 — field names follow the public v2 docs; "
        "confirm with Health check",
        default_enabled=False,
        attribution="Job via The Muse",
    )

    async def search(self, ctx: SearchContext) -> list[JobRecord]:
        out: list[JobRecord] = []
        key = self.key("THEMUSE_API_KEY")
        for page in range(0, int(self.config.get("max_pages", 3))):
            if ctx.cancelled:
                break
            params: dict[str, Any] = {"page": page, "location": "Flexible / Remote"}
            if key:
                params["api_key"] = key
            data = await ctx.http.get_json(self.meta.endpoint, params=params, cache_ttl=self.meta.cache_ttl_s)
            for raw in data.get("results", []) or []:
                job = self.safe_normalize(raw)
                if job:
                    out.append(job)
            if page + 1 >= int(data.get("page_count") or 0):
                break
        return out

    def normalize(self, raw: dict[str, Any]) -> JobRecord:
        locs = ", ".join(l.get("name", "") for l in raw.get("locations") or [])
        levels = [l.get("name") for l in raw.get("levels") or []]
        cats = [c.get("name") for c in raw.get("categories") or []]
        url = (raw.get("refs") or {}).get("landing_page")
        return N.build(
            source_id=self.source_id,
            source_name=self.name,
            source_job_id=str(raw.get("id")),
            title=raw.get("name"),
            company=(raw.get("company") or {}).get("name"),
            location=locs,
            seniority=levels[0].lower() if levels else None,
            description=N.html_to_text(raw.get("contents")),
            url=url,
            apply_url=url,
            posted_at=N.to_iso(raw.get("publication_date")),
            tags=cats,
        )


def _num(v: Any) -> float | None:
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def search_url(base: str, q: str) -> str:
    return base.format(q=quote_plus(q))
