"""Common connector interface.

Every source implements:
  * ``meta``          – documentation/limits/terms for the integration matrix
  * ``search(ctx)``   – returns normalized ``JobRecord`` objects (handles its own pagination)
  * ``normalize(raw)`` – converts one raw item into a ``JobRecord``
  * ``health_check(http)`` – cheap live probe

Connectors must never raise for a single bad record; they skip it and count it.
Network failures raise and are caught per-source by the search engine, so one
failing source never stops the whole search.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar, Optional

from ..config import get_secret
from ..http_client import HttpClient
from ..models import JobRecord, SearchFilters


@dataclass
class SourceMeta:
    kind: str
    name: str
    source_type: str  # Public API | ATS job board API | RSS feed | Career page | Key-based API
    docs_url: str
    endpoint: str
    auth: str = "None"
    free_tier: str = "Free, no key"
    rate_limit: str = "Not published — app self-limits"
    coverage: str = "Worldwide"
    filters: str = ""
    terms: str = ""
    required_keys: list[str] = field(default_factory=list)
    min_interval_s: float = 1.0  # minimum spacing between requests to this host
    cache_ttl_s: int = 6 * 3600
    verification: str = ""  # how/when the endpoint was verified
    default_enabled: bool = True
    attribution: str = ""  # shown next to jobs from this source


@dataclass
class SearchContext:
    http: HttpClient
    queries: list[str]
    filters: SearchFilters
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    progress: Optional[Callable[[str], None]] = None
    max_items: int = 600

    @property
    def cancelled(self) -> bool:
        return self.cancel.is_set()


class SkipSource(Exception):
    """Raised when a source cannot run (e.g. missing API key)."""


class BaseConnector:
    meta: ClassVar[SourceMeta]

    def __init__(self, source_id: str, name: str | None = None, config: dict[str, Any] | None = None):
        self.source_id = source_id
        self.name = name or self.meta.name
        self.config = config or {}
        self.skipped_records = 0

    # ------------------------------------------------------------------ keys
    def is_configured(self) -> tuple[bool, str]:
        """Board/feed connectors override this when they have nothing to query."""
        return True, ""

    def missing_keys(self) -> list[str]:
        return [k for k in self.meta.required_keys if not get_secret(k)]

    def key(self, name: str) -> str:
        return get_secret(name)

    def prepare(self, http: HttpClient) -> None:
        from urllib.parse import urlparse

        host = urlparse(self.meta.endpoint.split("{")[0]).netloc
        if host:
            http.set_min_interval(host, self.meta.min_interval_s)

    # ---------------------------------------------------------------- search
    async def search(self, ctx: SearchContext) -> list[JobRecord]:  # pragma: no cover - interface
        raise NotImplementedError

    def normalize(self, raw: Any) -> JobRecord | None:  # pragma: no cover - interface
        raise NotImplementedError

    def safe_normalize(self, raw: Any) -> JobRecord | None:
        try:
            return self.normalize(raw)
        except Exception:
            self.skipped_records += 1
            return None

    async def health_check(self, http: HttpClient) -> tuple[bool, str]:
        """Default: run a tiny search."""
        missing = self.missing_keys()
        if missing:
            return False, f"Needs configuration: {', '.join(missing)}"
        ctx = SearchContext(http=http, queries=["translator"], filters=SearchFilters(), max_items=5)
        jobs = await self.search(ctx)
        return True, f"OK — {len(jobs)} listing(s) returned"
