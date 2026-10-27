"""APIs that need a free registration / API key. They stay idle (and say so)
until the key is configured in .env or Settings."""
from __future__ import annotations

from typing import Any

from .. import job_normalizer as N
from ..models import JobRecord
from .base import BaseConnector, SearchContext, SkipSource, SourceMeta


class AdzunaConnector(BaseConnector):
    meta = SourceMeta(
        kind="adzuna",
        name="Adzuna",
        source_type="Key-based API",
        docs_url="https://developer.adzuna.com/",
        endpoint="https://api.adzuna.com/v1/api/jobs/{country}/search/{page}",
        auth="Free app_id + app_key (registration)",
        free_tier="Free registration; default 25 calls/min, 250/day, 1,000/week, 2,500/month. "
        "Commercial users get a 14-day trial.",
        rate_limit="25/min, 250/day (app: 3s spacing, ≤ 2 pages per query)",
        coverage="Country-specific (configure e.g. in, gb, us)",
        filters="what, where, max_days_old, results_per_page, salary_min, full_time/part_time/contract",
        terms="Personal/non-commercial use within free limits; attribute Adzuna.",
        required_keys=["ADZUNA_APP_ID", "ADZUNA_APP_KEY"],
        min_interval_s=3,
        cache_ttl_s=6 * 3600,
        verification="Limits per secondary documentation reviewed 2026-10-27; verify with Health check after adding keys",
        attribution="Jobs by Adzuna",
    )

    async def search(self, ctx: SearchContext) -> list[JobRecord]:
        if self.missing_keys():
            raise SkipSource("Add ADZUNA_APP_ID and ADZUNA_APP_KEY in Settings to enable Adzuna")
        out: list[JobRecord] = []
        countries = self.config.get("countries", ["in", "gb"])
        days = ctx.filters.posted_within_days or 30
        for country in countries:
            for q in ctx.queries[: int(self.config.get("max_queries", 6))]:
                if ctx.cancelled:
                    return out
                params = {
                    "app_id": self.key("ADZUNA_APP_ID"),
                    "app_key": self.key("ADZUNA_APP_KEY"),
                    "what": q,
                    "results_per_page": 50,
                    "max_days_old": days,
                    "content-type": "application/json",
                }
                data = await ctx.http.get_json(
                    f"https://api.adzuna.com/v1/api/jobs/{country}/search/1", params=params, cache_ttl=self.meta.cache_ttl_s
                )
                for raw in data.get("results", []) or []:
                    raw["_country"] = country
                    job = self.safe_normalize(raw)
                    if job:
                        out.append(job)
        return out

    def normalize(self, raw: dict[str, Any]) -> JobRecord:
        loc = (raw.get("location") or {}).get("display_name")
        predicted = str(raw.get("salary_is_predicted")) == "1"
        cur = {"in": "INR", "gb": "GBP", "us": "USD"}.get(raw["_country"], None)
        return N.build(
            source_id=self.source_id,
            source_name=self.name,
            source_job_id=str(raw.get("id")),
            title=N.html_to_text(raw.get("title")),
            company=(raw.get("company") or {}).get("display_name"),
            location=loc,
            eligible_regions=N.classify_regions(loc, "India" if raw["_country"] == "in" else None),
            job_type_hint=" ".join(filter(None, [raw.get("contract_time"), raw.get("contract_type")])),
            description=N.html_to_text(raw.get("description")),
            url=raw.get("redirect_url"),
            apply_url=raw.get("redirect_url"),
            posted_at=N.to_iso(raw.get("created")),
            salary_min=None if predicted else raw.get("salary_min"),
            salary_max=None if predicted else raw.get("salary_max"),
            salary_currency=None if predicted else cur,
            salary_period="year" if raw.get("salary_min") and not predicted else None,
            salary_text="Estimated by Adzuna" if predicted else None,
            tags=[(raw.get("category") or {}).get("label", "")],
        )


class USAJobsConnector(BaseConnector):
    meta = SourceMeta(
        kind="usajobs",
        name="USAJOBS",
        source_type="Key-based API",
        docs_url="https://developer.usajobs.gov/tutorials/search-jobs",
        endpoint="https://data.usajobs.gov/api/search",
        auth="Free Authorization-Key + your email as User-Agent",
        free_tier="Free for registered developers",
        rate_limit="Not stated in tutorial (app: 2s spacing)",
        coverage="US federal government jobs only — most require US citizenship",
        filters="Keyword, LocationName, JobCategoryCode, RemoteIndicator, ResultsPerPage, Page",
        terms="US government data; most roles restricted to US citizens (scored as Not Eligible when so).",
        required_keys=["USAJOBS_API_KEY", "USAJOBS_EMAIL"],
        min_interval_s=2,
        cache_ttl_s=12 * 3600,
        verification="Official tutorial reviewed 2026-10-27",
        default_enabled=False,
        attribution="USAJOBS.gov",
    )

    async def search(self, ctx: SearchContext) -> list[JobRecord]:
        if self.missing_keys():
            raise SkipSource("Add USAJOBS_API_KEY and USAJOBS_EMAIL in Settings to enable USAJOBS")
        headers = {
            "Host": "data.usajobs.gov",
            "User-Agent": self.key("USAJOBS_EMAIL"),
            "Authorization-Key": self.key("USAJOBS_API_KEY"),
        }
        out: list[JobRecord] = []
        for q in ctx.queries[:4]:
            if ctx.cancelled:
                break
            data = await ctx.http.get_json(
                self.meta.endpoint,
                params={"Keyword": q, "ResultsPerPage": 100},
                headers=headers,
                cache_ttl=self.meta.cache_ttl_s,
            )
            for item in (data.get("SearchResult") or {}).get("SearchResultItems", []) or []:
                job = self.safe_normalize(item.get("MatchedObjectDescriptor") or {})
                if job:
                    out.append(job)
        return out

    def normalize(self, d: dict[str, Any]) -> JobRecord:
        rem = (d.get("PositionRemuneration") or [{}])[0]
        details = ((d.get("UserArea") or {}).get("Details") or {})
        apply = d.get("ApplyURI") or []
        return N.build(
            source_id=self.source_id,
            source_name=self.name,
            source_job_id=d.get("PositionID"),
            title=d.get("PositionTitle"),
            company=d.get("OrganizationName"),
            location=d.get("PositionLocationDisplay"),
            eligible_regions=["USA"],
            job_type_hint=" ".join(s.get("Name", "") for s in d.get("PositionSchedule") or []),
            description="\n\n".join(filter(None, [details.get("JobSummary"), d.get("QualificationSummary")]))
            + "\n\nMost federal positions require U.S. citizenship.",
            url=d.get("PositionURI"),
            apply_url=apply[0] if apply else d.get("PositionURI"),
            posted_at=N.to_iso(d.get("PublicationStartDate")),
            deadline=N.to_iso(d.get("ApplicationCloseDate")),
            salary_min=_f(rem.get("MinimumRange")),
            salary_max=_f(rem.get("MaximumRange")),
            salary_currency="USD",
            salary_period={"PA": "year", "PH": "hour"}.get(rem.get("RateIntervalCode"), None),
        )


class JoobleConnector(BaseConnector):
    meta = SourceMeta(
        kind="jooble",
        name="Jooble",
        source_type="Key-based API",
        docs_url="https://jooble.org/api/about",
        endpoint="https://jooble.org/api/{key}",
        auth="Free API key via request form",
        free_tier="Free key on request (limits set per key)",
        rate_limit="Per key (app: 2s spacing)",
        coverage="Aggregator, ~70 countries incl. India",
        filters="keywords, location, page, salary, datecreatedfrom",
        terms="Display Jooble results with attribution per your key agreement.",
        required_keys=["JOOBLE_API_KEY"],
        min_interval_s=2,
        cache_ttl_s=6 * 3600,
        verification="Key-request page reviewed 2026-10-27; request/response fields follow Jooble's sample code — "
        "confirm with Health check after adding key",
        attribution="Jobs via Jooble",
    )

    async def search(self, ctx: SearchContext) -> list[JobRecord]:
        if self.missing_keys():
            raise SkipSource("Add JOOBLE_API_KEY in Settings to enable Jooble")
        out: list[JobRecord] = []
        url = f"https://jooble.org/api/{self.key('JOOBLE_API_KEY')}"
        for q in ctx.queries[: int(self.config.get("max_queries", 6))]:
            for loc in self.config.get("locations", ["India", "Remote"]):
                if ctx.cancelled:
                    return out
                data = await ctx.http.post_json(url, {"keywords": q, "location": loc, "page": "1"}, cache_ttl=self.meta.cache_ttl_s)
                for raw in data.get("jobs", []) or []:
                    job = self.safe_normalize(raw)
                    if job:
                        out.append(job)
        return out

    def normalize(self, raw: dict[str, Any]) -> JobRecord:
        sal = N.parse_salary_text(raw.get("salary"))
        return N.build(
            source_id=self.source_id,
            source_name=self.name,
            source_job_id=str(raw.get("id")),
            title=N.html_to_text(raw.get("title")),
            company=raw.get("company"),
            location=raw.get("location"),
            job_type_hint=raw.get("type"),
            description=N.html_to_text(raw.get("snippet")),
            url=raw.get("link"),
            apply_url=raw.get("link"),
            posted_at=N.to_iso(raw.get("updated")),
            tags=[raw.get("source") or ""],
            **sal,
        )


def _f(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
