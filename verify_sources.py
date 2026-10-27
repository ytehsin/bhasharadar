"""Live check of every enabled job source from YOUR machine.

    python verify_sources.py

Runs each connector's health check against the real endpoint (no cache),
records the result in the database (shown on the Sources page) and prints an
honest table of what works right now. Nothing is applied for or emailed.
"""
from __future__ import annotations

import asyncio

from app import db
from app.http_client import HttpClient
from app.job_source_registry import get_connector, record_result, seed_sources


async def main() -> None:
    db.migrate()
    seed_sources()
    rows = db.query("SELECT id, name, enabled FROM sources ORDER BY name")
    print(f"{'Source':32} {'Result':8} Details")
    print("-" * 100)
    working = 0
    for r in rows:
        conn = get_connector(r["id"])
        if not r["enabled"]:
            print(f"{r['name']:32} {'off':8} disabled in Settings → Sources")
            continue
        http = HttpClient(max_retries=1, cache_ttl=0)
        conn.prepare(http)
        try:
            ok, msg = await asyncio.wait_for(conn.health_check(http), timeout=90)
        except Exception as exc:  # report, never crash
            ok, msg = False, f"{type(exc).__name__}: {exc}"
        finally:
            await http.aclose()
        record_result(r["id"], ok=ok, error=None if ok else msg, health="needs_key" if "Needs configuration" in msg else None)
        working += ok
        print(f"{r['name']:32} {'OK' if ok else 'FAIL':8} {msg[:120]}")
    print("-" * 100)
    print(f"{working} source(s) verified working.")


if __name__ == "__main__":
    asyncio.run(main())
