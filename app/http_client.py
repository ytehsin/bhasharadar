"""Shared async HTTP layer: caching, per-host rate limiting, retries with
exponential backoff, timeouts and robots.txt checks."""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
import time
from typing import Any
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from . import config, db


class RateLimited(Exception):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class FetchError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class HttpClient:
    """One instance per search session."""

    def __init__(
        self,
        *,
        timeout: float = config.HTTP_TIMEOUT,
        max_retries: int = 3,
        cache_ttl: int = config.CACHE_TTL_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
        backoff_base: float = 1.0,
        respect_intervals: bool = True,
    ):
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            headers={"User-Agent": config.USER_AGENT, "Accept": "application/json, text/xml, text/html;q=0.8"},
            follow_redirects=True,
            transport=transport,
        )
        self.max_retries = max_retries
        self.cache_ttl = cache_ttl
        self.backoff_base = backoff_base
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._host_last: dict[str, float] = {}
        self._host_interval: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}
        self.stats = {"requests": 0, "cache_hits": 0, "retries": 0}
        self.respect_intervals = respect_intervals

    async def aclose(self) -> None:
        await self._client.aclose()

    def set_min_interval(self, host: str, seconds: float) -> None:
        self._host_interval[host] = max(self._host_interval.get(host, 0), seconds)

    # ---- cache -----------------------------------------------------------
    @staticmethod
    def _cache_key(method: str, url: str, params: Any, body: Any) -> str:
        raw = json.dumps([method, url, params, body], sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    def _cache_get(self, key: str, ttl: int) -> str | None:
        if ttl <= 0:
            return None
        row = db.query_one("SELECT fetched_at, body FROM http_cache WHERE key=?", (key,))
        if row and time.time() - row["fetched_at"] < ttl:
            self.stats["cache_hits"] += 1
            return row["body"]
        return None

    def _cache_put(self, key: str, status: int, body: str) -> None:
        db.execute(
            "INSERT INTO http_cache(key,fetched_at,status,body) VALUES(?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET fetched_at=excluded.fetched_at,status=excluded.status,body=excluded.body",
            (key, time.time(), status, body),
        )

    # ---- rate limiting -----------------------------------------------------
    async def _throttle(self, host: str) -> None:
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with lock:
            interval = self._host_interval.get(host, 0.5) if self.respect_intervals else 0
            wait = self._host_last.get(host, 0) + interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._host_last[host] = time.monotonic()

    # ---- robots.txt ----------------------------------------------------------
    async def allowed_by_robots(self, url: str) -> bool:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        if base not in self._robots:
            rp: RobotFileParser | None = RobotFileParser()
            try:
                resp = await self._client.get(base + "/robots.txt", timeout=10)
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                elif resp.status_code in (401, 403):
                    rp.disallow_all = True
                else:
                    rp = None  # no robots.txt => allowed
            except httpx.HTTPError:
                rp = None
            self._robots[base] = rp
        rp = self._robots[base]
        return True if rp is None else rp.can_fetch(config.USER_AGENT, url)

    # ---- core request ------------------------------------------------------------
    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        json_body: Any = None,
        headers: dict | None = None,
        cache_ttl: int | None = None,
        check_robots: bool = False,
    ) -> str:
        # A client created with cache_ttl=0 (health checks, forced refresh) never uses the cache.
        ttl = 0 if self.cache_ttl <= 0 else (self.cache_ttl if cache_ttl is None else cache_ttl)
        key = self._cache_key(method, url, params, json_body)
        cached = self._cache_get(key, ttl)
        if cached is not None:
            return cached
        if check_robots and not await self.allowed_by_robots(url):
            raise FetchError("Blocked by robots.txt", status=None)
        host = urlparse(url).netloc
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            await self._throttle(host)
            try:
                self.stats["requests"] += 1
                resp = await self._client.request(method, url, params=params, json=json_body, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = FetchError(f"Network error: {type(exc).__name__}")
            else:
                if resp.status_code == 429:
                    ra = resp.headers.get("Retry-After")
                    retry_after = float(ra) if ra and ra.replace(".", "", 1).isdigit() else None
                    last_exc = RateLimited("Rate limited by source (HTTP 429)", retry_after)
                    if retry_after and retry_after > 30:
                        raise last_exc
                elif resp.status_code >= 500:
                    last_exc = FetchError(f"Server error HTTP {resp.status_code}", resp.status_code)
                elif resp.status_code >= 400:
                    raise FetchError(f"HTTP {resp.status_code}", resp.status_code)
                else:
                    body = resp.text
                    if ttl > 0:
                        self._cache_put(key, resp.status_code, body)
                    return body
            if attempt < self.max_retries:
                self.stats["retries"] += 1
                delay = self.backoff_base * (2**attempt) + random.uniform(0, 0.25 * self.backoff_base)
                if isinstance(last_exc, RateLimited) and last_exc.retry_after:
                    delay = max(delay, last_exc.retry_after)
                await asyncio.sleep(delay)
        assert last_exc is not None
        raise last_exc

    async def get_json(self, url: str, **kw: Any) -> Any:
        text = await self.request("GET", url, **kw)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise FetchError("Response was not valid JSON") from exc

    async def post_json(self, url: str, body: Any, **kw: Any) -> Any:
        text = await self.request("POST", url, json_body=body, **kw)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise FetchError("Response was not valid JSON") from exc

    async def get_text(self, url: str, **kw: Any) -> str:
        return await self.request("GET", url, **kw)

    async def head_status(self, url: str) -> int | None:
        """Lightweight liveness check used for expiry verification."""
        try:
            await self._throttle(urlparse(url).netloc)
            resp = await self._client.get(url, timeout=12)
            return resp.status_code
        except httpx.HTTPError:
            return None
