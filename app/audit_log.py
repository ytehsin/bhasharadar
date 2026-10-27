"""Audit log. Never records secrets, tokens or full resume/email bodies."""
from __future__ import annotations

import json
import re
from typing import Any

from . import db

_SECRET_KEYS = re.compile(r"(pass|secret|token|key|authorization|cookie)", re.I)


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: ("***" if _SECRET_KEYS.search(str(k)) else _scrub(v)) for k, v in value.items()
        }
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, str):
        # Strip anything that looks like a credential in query strings
        value = re.sub(r"(app_key|api_key|apikey|key|token)=([^&\s]+)", r"\1=***", value, flags=re.I)
        return value[:500]
    return value


def log(category: str, action: str, **detail: Any) -> None:
    try:
        db.execute(
            "INSERT INTO audit_log(ts,category,action,detail) VALUES(?,?,?,?)",
            (db.now_iso(), category, action, json.dumps(_scrub(detail), default=str)),
        )
    except Exception:  # audit logging must never break the app
        pass


def recent(limit: int = 200, category: str | None = None) -> list[dict]:
    if category:
        rows = db.query(
            "SELECT * FROM audit_log WHERE category=? ORDER BY id DESC LIMIT ?", (category, limit)
        )
    else:
        rows = db.query("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
    for r in rows:
        r["detail"] = db.jload(r["detail"], {})
    return rows
