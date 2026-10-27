"""Public ATS job-board APIs (Greenhouse, Lever, Ashby, Workable, SmartRecruiters).

Each source holds a list of company boards in its config:
    {"boards": [{"token": "scaleai", "company": "Scale AI"}]}
You can add boards in Settings → Sources without touching code.
"""
from __future__ import annotations

import re
from typing import Any

from .. import job_normalizer as N
from ..http_client import FetchError
from ..models import JobRecord, SearchFilters
from .base import BaseConnector, SearchContext, SourceMeta

_TOKEN_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


class _BoardConnector(BaseConnector):
    def boards(self) -> list[dict[str, Any]]:
        out = []
        for b in self.config.get("boards", []):
            if isinstance(b, str):
                b = {"token": b}
            tok = str(b.get("token", "")).strip()
            if _TOKEN_RE.match(tok):
                out.append({"token": tok, "company": b.get("company") or tok, "eu": bool(b.get("eu"))})
        return out

    def is_configured(self) -> tuple[bool, str]:
        return (True, "") if self.boards() else (False, "No company boards added yet (Settings → Sources)")

    async def fetch_board(self, ctx: SearchContext, board: dict[str, Any]) -> list[JobRecord]:  # pragma: no cover
        raise NotImplementedError

    async def search(self, ctx: SearchContext) -> list[JobRecord]:
        out: list[JobRecord] = []
        self.board_errors: dict[str, str] = {}
        boards = self.boards()
        for board in boards:
            if ctx.cancelled:
                break
            try:
                out.extend(await self.fetch_board(ctx, board))
            except FetchError as exc:  # one bad board must not sink the others
                self.board_errors[board["token"]] = str(exc)
        if boards and len(self.board_errors) == len(boards):
            raise FetchError("All boards failed: " + "; ".join(f"{k}: {v}" for k, v in self.board_errors.items()))
        return out

    async def health_check(self, http) -> tuple[bool, str]:
        boards = self.boards()
        if not boards:
            return False, "No company boards configured — add board tokens in Settings → Sources"
        ok, bad = [], []
        ctx = SearchContext(http=http, queries=[], filters=SearchFilters())
        for b in boards:
            try:
                jobs = await self.fetch_board(ctx, b)
                ok.append(f"{b['company']} ({len(jobs)})")
            except FetchError as exc:
                bad.append(f"{b['company']}: {exc}")
        msg = ("OK: " + ", ".join(ok)) if ok else ""
        if bad:
            msg += (" | " if msg else "") + "Failed: " + ", ".join(bad)
        return bool(ok), msg


# -------------------------------------------------------------- Greenhouse ----
class GreenhouseConnector(_BoardConnector):
    meta = SourceMeta(
        kind="greenhouse",
        name="Greenhouse boards",
        source_type="ATS job board API",
        docs_url="https://developers.greenhouse.io/job-board.html",
        endpoint="https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true",
        rate_limit="No published limit for GET (app: 1s spacing, cached 3h)",
        coverage="Any company using Greenhouse (one board token per company)",
        filters="none server-side (filtered locally)",
        terms="Public, unauthenticated Job Board API intended for displaying a company's openings.",
        min_interval_s=1,
        cache_ttl_s=3 * 3600,
        verification="Endpoint per Greenhouse docs; boards 'scaleai' and 'agency' confirmed live via public job pages 2026-10-27",
        attribution="Company careers page (Greenhouse)",
    )

    async def fetch_board(self, ctx, board):
        host = "boards-api.eu.greenhouse.io" if board["eu"] else "boards-api.greenhouse.io"
        url = f"https://{host}/v1/boards/{board['token']}/jobs"
        data = await ctx.http.get_json(url, params={"content": "true"}, cache_ttl=self.meta.cache_ttl_s)
        out = []
        for raw in data.get("jobs", []) or []:
            raw["_company"] = raw.get("company_name") or board["company"]
            job = self.safe_normalize(raw)
            if job:
                out.append(job)
        return out

    def normalize(self, raw: dict[str, Any]) -> JobRecord:
        loc = (raw.get("location") or {}).get("name")
        offices = ", ".join(o.get("name", "") for o in raw.get("offices") or [])
        depts = [d.get("name") for d in raw.get("departments") or [] if d.get("name")]
        return N.build(
            source_id=self.source_id,
            source_name=self.name,
            source_job_id=str(raw.get("id")),
            title=raw.get("title"),
            company=raw["_company"],
            location=loc or offices,
            description=N.html_to_text(raw.get("content")),
            url=raw.get("absolute_url"),
            apply_url=raw.get("absolute_url"),
            posted_at=N.to_iso(raw.get("first_published") or raw.get("updated_at")),
            tags=depts,
        )


# ------------------------------------------------------------------- Lever ----
class LeverConnector(_BoardConnector):
    meta = SourceMeta(
        kind="lever",
        name="Lever boards",
        source_type="ATS job board API",
        docs_url="https://github.com/lever/postings-api",
        endpoint="https://api.lever.co/v0/postings/{company}?mode=json",
        rate_limit="No published read limit (app: 1s spacing, cached 3h)",
        coverage="Any company using Lever (EU host supported)",
        filters="skip, limit, team, location, commitment",
        terms="Public Postings API; link to hostedUrl/applyUrl.",
        min_interval_s=1,
        cache_ttl_s=3 * 3600,
        verification="Endpoint per Lever postings docs; sites 'weloglobal' (Welocalize) and 'rws' (RWS TrainAI) "
        "confirmed via live Hindi postings 2026-10-27",
        attribution="Company careers page (Lever)",
    )

    async def fetch_board(self, ctx, board):
        host = "api.eu.lever.co" if board["eu"] else "api.lever.co"
        out: list[JobRecord] = []
        skip, limit = 0, 100
        while True:
            data = await ctx.http.get_json(
                f"https://{host}/v0/postings/{board['token']}",
                params={"mode": "json", "skip": skip, "limit": limit},
                cache_ttl=self.meta.cache_ttl_s,
            )
            items = data if isinstance(data, list) else []
            for raw in items:
                raw["_company"] = board["company"]
                job = self.safe_normalize(raw)
                if job:
                    out.append(job)
            if len(items) < limit or skip > 2000 or ctx.cancelled:
                break
            skip += limit
        return out

    def normalize(self, raw: dict[str, Any]) -> JobRecord:
        cat = raw.get("categories") or {}
        all_locs = cat.get("allLocations") or []
        loc = cat.get("location") or ", ".join(all_locs)
        desc_parts = [raw.get("descriptionPlain") or N.html_to_text(raw.get("description"))]
        for lst in raw.get("lists") or []:
            desc_parts.append(f"{lst.get('text', '')}:\n{N.html_to_text(lst.get('content'))}")
        desc_parts.append(raw.get("additionalPlain") or "")
        sal = raw.get("salaryRange") or {}
        return N.build(
            source_id=self.source_id,
            source_name=self.name,
            source_job_id=raw.get("id"),
            title=raw.get("text"),
            company=raw["_company"],
            location=loc,
            remote_type=N.classify_remote(explicit=raw.get("workplaceType")) if raw.get("workplaceType") else "unknown",
            eligible_regions=N.classify_regions(loc, raw.get("country")),
            job_type_hint=cat.get("commitment"),
            description="\n\n".join(p for p in desc_parts if p),
            url=raw.get("hostedUrl"),
            apply_url=raw.get("applyUrl") or raw.get("hostedUrl"),
            posted_at=N.to_iso(raw.get("createdAt")),
            salary_min=sal.get("min"),
            salary_max=sal.get("max"),
            salary_currency=sal.get("currency"),
            salary_period=(re.search(r"(hour|day|week|month|year)", sal.get("interval") or "") or [None])[0],
            tags=[t for t in (cat.get("team"), cat.get("department")) if t],
        )


# ------------------------------------------------------------------- Ashby ----
class AshbyConnector(_BoardConnector):
    meta = SourceMeta(
        kind="ashby",
        name="Ashby boards",
        source_type="ATS job board API",
        docs_url="https://developers.ashbyhq.com/docs/public-job-posting-api",
        endpoint="https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true",
        rate_limit="No published limit (app: 1s spacing, cached 3h)",
        coverage="Any company using Ashby",
        filters="includeCompensation",
        terms="Public posting API; link to the job/apply URL.",
        min_interval_s=1,
        cache_ttl_s=3 * 3600,
        verification="Endpoint per Ashby developer docs (secondary source reviewed 2026-10-27). "
        "No relevant default boards yet — add your own.",
        attribution="Company careers page (Ashby)",
    )

    async def fetch_board(self, ctx, board):
        data = await ctx.http.get_json(
            f"https://api.ashbyhq.com/posting-api/job-board/{board['token']}",
            params={"includeCompensation": "true"},
            cache_ttl=self.meta.cache_ttl_s,
        )
        out = []
        for raw in data.get("jobs", []) or []:
            if raw.get("isListed") is False:
                continue
            raw["_company"] = board["company"]
            job = self.safe_normalize(raw)
            if job:
                out.append(job)
        return out

    def normalize(self, raw: dict[str, Any]) -> JobRecord:
        locs = [raw.get("location")] + [
            (s.get("location") if isinstance(s, dict) else s) for s in raw.get("secondaryLocations") or []
        ]
        loc = ", ".join(l for l in locs if l)
        comp = raw.get("compensation") or {}
        remote = raw.get("workplaceType") or (True if raw.get("isRemote") else None)
        return N.build(
            source_id=self.source_id,
            source_name=self.name,
            source_job_id=raw.get("id") or raw.get("jobUrl"),
            title=raw.get("title"),
            company=raw["_company"],
            location=loc,
            remote_type=N.classify_remote(explicit=remote) if remote is not None else "unknown",
            job_type_hint=raw.get("employmentType"),
            description=raw.get("descriptionPlain") or N.html_to_text(raw.get("descriptionHtml")),
            url=raw.get("jobUrl"),
            apply_url=raw.get("applyUrl") or raw.get("jobUrl"),
            posted_at=N.to_iso(raw.get("publishedAt")),
            salary_text=comp.get("compensationTierSummary") or comp.get("scrapeableCompensationSalarySummary"),
            tags=[t for t in (raw.get("department"), raw.get("team")) if t],
        )


# ---------------------------------------------------------------- Workable ----
class WorkableConnector(_BoardConnector):
    meta = SourceMeta(
        kind="workable",
        name="Workable boards",
        source_type="ATS job board API",
        docs_url="https://dev.to/zsevic/integration-with-workable-public-jobs-api-3nk4",
        endpoint="https://www.workable.com/api/accounts/{slug}?details=true",
        rate_limit="Not published (app: 2s spacing, cached 3h)",
        coverage="Any company using Workable",
        filters="details=true",
        terms="Public account feed used by Workable career widgets. Community-documented, not an official API.",
        min_interval_s=2,
        cache_ttl_s=3 * 3600,
        verification="Community documentation reviewed 2026-10-27; not officially documented by Workable",
        attribution="Company careers page (Workable)",
    )

    async def fetch_board(self, ctx, board):
        data = await ctx.http.get_json(
            f"https://www.workable.com/api/accounts/{board['token']}",
            params={"details": "true"},
            cache_ttl=self.meta.cache_ttl_s,
        )
        out = []
        company = data.get("name") or board["company"]
        for raw in data.get("jobs", []) or []:
            raw["_company"] = company
            job = self.safe_normalize(raw)
            if job:
                out.append(job)
        return out

    def normalize(self, raw: dict[str, Any]) -> JobRecord:
        locobj = raw.get("location") or {}
        loc = locobj.get("location_str") if isinstance(locobj, dict) else None
        if not loc:
            loc = ", ".join(
                filter(None, [raw.get("city"), raw.get("state"), raw.get("country")])
            )
        wpt = (locobj.get("workplace_type") if isinstance(locobj, dict) else None) or (
            "remote" if raw.get("telecommuting") else None
        )
        return N.build(
            source_id=self.source_id,
            source_name=self.name,
            source_job_id=raw.get("shortcode") or str(raw.get("id")),
            title=raw.get("title"),
            company=raw["_company"],
            location=loc,
            remote_type=N.classify_remote(explicit=wpt) if wpt else "unknown",
            job_type_hint=raw.get("employment_type"),
            seniority=(raw.get("experience") or None),
            description=N.html_to_text(raw.get("full_description") or raw.get("description")),
            url=raw.get("url") or raw.get("shortlink"),
            apply_url=raw.get("application_url") or raw.get("url"),
            posted_at=N.to_iso(raw.get("published_on") or raw.get("created_at")),
        )


# ---------------------------------------------------------- SmartRecruiters ----
class SmartRecruitersConnector(_BoardConnector):
    meta = SourceMeta(
        kind="smartrecruiters",
        name="SmartRecruiters boards",
        source_type="ATS job board API",
        docs_url="https://developers.smartrecruiters.com/docs/posting-api",
        endpoint="https://api.smartrecruiters.com/v1/companies/{company}/postings",
        rate_limit="Per-IP limits documented by SmartRecruiters (app: 1s spacing, cached 3h)",
        coverage="Companies whose plan enables the public Posting API",
        filters="limit, offset",
        terms="Public Posting API; some companies return nothing if the feed is not enabled.",
        min_interval_s=1,
        cache_ttl_s=3 * 3600,
        verification="Endpoint per secondary documentation reviewed 2026-10-27; no default boards",
        attribution="Company careers page (SmartRecruiters)",
    )

    async def fetch_board(self, ctx, board):
        out: list[JobRecord] = []
        offset = 0
        while True:
            data = await ctx.http.get_json(
                f"https://api.smartrecruiters.com/v1/companies/{board['token']}/postings",
                params={"limit": 100, "offset": offset},
                cache_ttl=self.meta.cache_ttl_s,
            )
            items = data.get("content", []) or []
            for raw in items:
                raw["_company"] = (raw.get("company") or {}).get("name") or board["company"]
                raw["_token"] = board["token"]
                job = self.safe_normalize(raw)
                if job:
                    out.append(job)
            offset += len(items)
            if not items or offset >= int(data.get("totalFound") or 0) or offset > 1000 or ctx.cancelled:
                break
        return out

    def normalize(self, raw: dict[str, Any]) -> JobRecord:
        loc = raw.get("location") or {}
        loc_s = ", ".join(filter(None, [loc.get("city"), loc.get("region"), loc.get("country")]))
        url = f"https://jobs.smartrecruiters.com/{raw['_token']}/{raw.get('id')}"
        return N.build(
            source_id=self.source_id,
            source_name=self.name,
            source_job_id=str(raw.get("id")),
            title=raw.get("name"),
            company=raw["_company"],
            location=loc_s,
            remote_type="remote" if loc.get("remote") else "unknown",
            job_type_hint=(raw.get("typeOfEmployment") or {}).get("label"),
            seniority=((raw.get("experienceLevel") or {}).get("label") or None),
            description="",  # list endpoint has no description; detail fetched on demand
            url=url,
            apply_url=url,
            posted_at=N.to_iso(raw.get("releasedDate")),
        )
