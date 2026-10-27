"""SQLite storage with simple, versioned migrations."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from . import config

_lock = threading.RLock()
_db_path: Path = config.DB_PATH
# Cloud mode: set DATABASE_URL to a Postgres URL (e.g. Neon). Otherwise a local SQLite file is used.
_database_url: str = os.getenv("DATABASE_URL", "").strip()
_pg_conn = None
SERIAL_TABLES = {"applications", "outreach", "audit_log"}

MIGRATIONS: list[str] = [
    # v1 — initial schema
    """
    CREATE TABLE IF NOT EXISTS profile (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        data TEXT NOT NULL,
        source TEXT NOT NULL,            -- 'seed' | 'resume'
        resume_filename TEXT,
        resume_text TEXT,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS sources (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,              -- connector type
        name TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        config TEXT NOT NULL DEFAULT '{}',
        builtin INTEGER NOT NULL DEFAULT 1,
        health TEXT NOT NULL DEFAULT 'unknown',  -- ok | error | needs_key | unknown | rate_limited
        last_success_at TEXT,
        last_error TEXT,
        last_count INTEGER DEFAULT 0,
        last_run_at TEXT,
        last_call_at TEXT
    );
    CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY,
        fingerprint TEXT NOT NULL,
        source_id TEXT NOT NULL,
        source_name TEXT NOT NULL,
        source_job_id TEXT,
        title TEXT NOT NULL,
        company TEXT,
        location TEXT,
        remote_type TEXT,                -- remote | hybrid | onsite | unknown
        eligible_regions TEXT,           -- json list
        job_type TEXT,                   -- full_time | part_time | contract | freelance | internship | unknown
        seniority TEXT,
        salary_min REAL, salary_max REAL, salary_currency TEXT, salary_period TEXT, salary_text TEXT,
        description TEXT,
        url TEXT,
        apply_url TEXT,
        posted_at TEXT,
        deadline TEXT,
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        last_verified_at TEXT,
        status TEXT NOT NULL DEFAULT 'active',  -- active | expired | unknown
        tags TEXT,
        also_on TEXT DEFAULT '[]',       -- json list of {source, url} duplicates
        match_score REAL,
        match_label TEXT,
        match_detail TEXT,
        saved INTEGER NOT NULL DEFAULT 0,
        hidden INTEGER NOT NULL DEFAULT 0,
        last_session_id TEXT,
        new_in_session TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_jobs_fp ON jobs(fingerprint);
    CREATE INDEX IF NOT EXISTS ix_jobs_score ON jobs(match_score DESC);
    CREATE TABLE IF NOT EXISTS search_sessions (
        id TEXT PRIMARY KEY,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        status TEXT NOT NULL,           -- running | completed | cancelled | failed
        stage TEXT,
        filters TEXT,
        stats TEXT,
        progress TEXT,
        checkpoint TEXT
    );
    CREATE TABLE IF NOT EXISTS applications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT NOT NULL,
        attempt INTEGER NOT NULL DEFAULT 1,
        status TEXT NOT NULL,
        method TEXT,
        title TEXT, company TEXT, source TEXT, url TEXT, match_score REAL,
        notes TEXT,
        confirmation TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        applied_at TEXT,
        followup_due TEXT,
        history TEXT NOT NULL DEFAULT '[]',
        UNIQUE(job_id, attempt)
    );
    CREATE TABLE IF NOT EXISTS outreach (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT,
        company TEXT,
        recipient TEXT,
        recipient_type TEXT,
        recipient_source TEXT,          -- where the address was publicly found
        subject TEXT,
        body TEXT,
        template TEXT,
        attach_resume INTEGER DEFAULT 0,
        status TEXT NOT NULL,           -- draft | pending_review | queued | sent | failed | cancelled | blocked
        mode TEXT,
        followup_of INTEGER,
        followup_number INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        scheduled_at TEXT,
        sent_at TEXT,
        error TEXT
    );
    CREATE TABLE IF NOT EXISTS suppression (
        email TEXT PRIMARY KEY,
        reason TEXT,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS secrets (
        name TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        category TEXT NOT NULL,
        action TEXT NOT NULL,
        detail TEXT
    );
    CREATE TABLE IF NOT EXISTS http_cache (
        key TEXT PRIMARY KEY,
        fetched_at REAL NOT NULL,
        status INTEGER,
        body TEXT
    );
    """,
    # v2 — uploaded files kept in the database (cloud hosts have no lasting disk)
    """
    CREATE TABLE IF NOT EXISTS files (
        name TEXT PRIMARY KEY,
        filename TEXT,
        data BLOB NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def is_postgres() -> bool:
    return _database_url.startswith(("postgres://", "postgresql://"))


def set_db_path(path: Path | str) -> None:
    """Used by tests to point at a temporary SQLite database."""
    global _db_path, _database_url
    _db_path = Path(path)
    _database_url = ""


def set_database_url(url: str) -> None:
    """Used by tests (and start-up) to switch to Postgres."""
    global _database_url, _pg_conn
    _database_url = url
    if _pg_conn is not None:
        try:
            _pg_conn.close()
        except Exception:
            pass
    _pg_conn = None


# ------------------------------------------------------------- Postgres ----
def _pg_sql(sql: str) -> str:
    """Translate the app's SQLite-flavoured SQL to Postgres."""
    sql = sql.replace("%", "%%").replace("?", "%s")
    return sql


def _pg_ddl(script: str) -> list[str]:
    script = re.sub(r"INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY", script)
    script = re.sub(r"\bREAL\b", "DOUBLE PRECISION", script)
    script = re.sub(r"\bBLOB\b", "BYTEA", script)
    return [st.strip() for st in script.split(";") if st.strip()]


def _clean_param(v: Any) -> Any:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, str) and "\x00" in v:
        return v.replace("\x00", "")
    return v


class _PGCursor:
    def __init__(self, cur):
        self._cur = cur

    def fetchone(self):
        try:
            return self._cur.fetchone()
        except Exception:
            return None

    def fetchall(self):
        try:
            return self._cur.fetchall()
        except Exception:
            return []

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    @property
    def lastrowid(self):
        return getattr(self, "_lastrowid", None)


class _PGConn:
    """Gives a psycopg connection the small sqlite3-style surface the app uses."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql: str, params: tuple | list = ()):
        q = _pg_sql(sql)
        returning = False
        m = re.match(r"\s*INSERT\s+INTO\s+(\w+)", sql, re.I)
        if m and m.group(1).lower() in SERIAL_TABLES and "RETURNING" not in sql.upper():
            q += " RETURNING id"
            returning = True
        cur = self._conn.cursor()
        cur.execute(q, [_clean_param(p) for p in params])
        wrapped = _PGCursor(cur)
        if returning:
            row = cur.fetchone()
            wrapped._lastrowid = row["id"] if row else None
        return wrapped

    def executescript(self, script: str) -> None:
        cur = self._conn.cursor()
        for st in _pg_ddl(script):
            cur.execute(st)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        try:
            self._conn.rollback()
        except Exception:
            pass

    def close(self) -> None:  # the shared connection stays open
        pass


def _pg_connect():
    global _pg_conn
    import psycopg
    from psycopg.rows import dict_row

    if _pg_conn is None or _pg_conn.closed:
        # prepare_threshold=None: safe with Neon's connection pooler (PgBouncer)
        _pg_conn = psycopg.connect(_database_url, row_factory=dict_row, connect_timeout=20,
                                   keepalives=1, keepalives_idle=60, prepare_threshold=None)
    return _pg_conn


# --------------------------------------------------------------- SQLite ----
def connect():
    if is_postgres():
        return _PGConn(_pg_connect())
    conn = sqlite3.connect(_db_path, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def tx() -> Iterator[Any]:
    with _lock:
        try:
            conn = connect()
        except Exception:
            if not is_postgres():
                raise
            set_database_url(_database_url)  # stale connection (e.g. Neon woke up) → reconnect once
            conn = connect()
        try:
            yield conn
            conn.commit()
        except Exception as exc:
            conn.rollback()
            if is_postgres() and type(exc).__module__.startswith("psycopg") and "Operational" in type(exc).__name__:
                set_database_url(_database_url)
            raise
        finally:
            conn.close()


def migrate() -> int:
    with tx() as c:
        c.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        row = c.execute("SELECT version FROM schema_version").fetchone()
        current = row["version"] if row else 0
        if row is None:
            c.execute("INSERT INTO schema_version(version) VALUES (0)")
        for idx in range(current, len(MIGRATIONS)):
            c.executescript(MIGRATIONS[idx])
            c.execute("UPDATE schema_version SET version=?", (idx + 1,))
        return len(MIGRATIONS)


def _retrying(fn):
    """One automatic retry when the cloud database dropped an idle connection."""
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except Exception as exc:
            if is_postgres() and "Operational" in type(exc).__name__:
                return fn(*a, **kw)
            raise
    return wrapper


@_retrying
def query(sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
    with tx() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def query_one(sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
    rows = query(sql, params)
    return rows[0] if rows else None


@_retrying
def execute(sql: str, params: tuple | list = ()) -> int:
    """INSERT → new row id (when the table has one); anything else → affected row count."""
    with tx() as c:
        cur = c.execute(sql, params)
        if re.match(r"\s*INSERT", sql, re.I) and cur.lastrowid:
            return cur.lastrowid
        return cur.rowcount


def get_setting(key: str, default: Any = None) -> Any:
    row = query_one("SELECT value FROM settings WHERE key=?", (key,))
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except json.JSONDecodeError:
        return default


def set_setting(key: str, value: Any) -> None:
    execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value)),
    )


def put_file(name: str, filename: str, data: bytes) -> None:
    execute(
        "INSERT INTO files(name,filename,data,updated_at) VALUES(?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
        "filename=excluded.filename, data=excluded.data, updated_at=excluded.updated_at",
        (name, filename, data, now_iso()),
    )


def get_file(name: str) -> tuple[str, bytes] | None:
    row = query_one("SELECT filename, data FROM files WHERE name=?", (name,))
    return (row["filename"], bytes(row["data"])) if row else None


def delete_file(name: str) -> None:
    execute("DELETE FROM files WHERE name=?", (name,))


def jload(value: str | None, default: Any = None) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default
