"""Optional scheduled searches. OFF by default — the app stays idle until you
press GO unless you explicitly enable a daily schedule in Settings."""
from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from . import db
from .audit_log import log
from .models import SearchFilters

DEFAULT = {"enabled": False, "time": "09:00", "tz": "Asia/Kolkata"}


def get_schedule() -> dict:
    return {**DEFAULT, **(db.get_setting("schedule", {}) or {})}


def due(now: datetime | None = None) -> bool:
    s = get_schedule()
    if not s["enabled"]:
        return False
    tz = ZoneInfo(s["tz"])
    now = (now or datetime.now(tz)).astimezone(tz)
    hh, mm = map(int, s["time"].split(":"))
    last = db.get_setting("schedule_last_run_date")
    return now.hour * 60 + now.minute >= hh * 60 + mm and last != now.date().isoformat()


async def scheduler_loop(stop_event: asyncio.Event) -> None:
    from .search_engine import manager

    while not stop_event.is_set():
        try:
            if due():
                s = get_schedule()
                db.set_setting("schedule_last_run_date", datetime.now(ZoneInfo(s["tz"])).date().isoformat())
                filters = SearchFilters(**(db.get_setting("last_filters", {}) or {}))
                manager.start(filters)
                log("scheduler", "scheduled_search_started")
        except RuntimeError:
            pass  # a search is already running
        except Exception as exc:
            log("scheduler", "error", error=type(exc).__name__)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=60)
        except asyncio.TimeoutError:
            pass
