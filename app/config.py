"""Application configuration.

All secrets come from environment variables (or the encrypted settings store);
nothing secret is hardcoded. See `.env.example`.
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no extra dependency). Existing env vars win."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(BASE_DIR / ".env")

APP_NAME = "BhashaRadar"
APP_VERSION = "1.0.0"

DATA_DIR = Path(os.getenv("BR_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = Path(os.getenv("BR_DB_PATH", DATA_DIR / "bhasharadar.db"))
UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
CACHE_TTL_SECONDS = int(os.getenv("BR_CACHE_TTL_SECONDS", "21600"))  # 6h
HTTP_TIMEOUT = float(os.getenv("BR_HTTP_TIMEOUT", "20"))
MAX_CONCURRENCY = int(os.getenv("BR_MAX_CONCURRENCY", "6"))
USER_AGENT = os.getenv(
    "BR_USER_AGENT",
    f"{APP_NAME}/{APP_VERSION} (personal job search tool; contact: set BR_CONTACT_EMAIL)",
)
CONTACT_EMAIL = os.getenv("BR_CONTACT_EMAIL", "")
HOST = os.getenv("BR_HOST", "127.0.0.1")
PORT = int(os.getenv("BR_PORT", os.getenv("PORT", "8000")))

# ---- Cloud mode (Render etc.) -------------------------------------------
# Turned on automatically on Render, or with BR_CLOUD=1.
CLOUD = bool(os.getenv("RENDER") or os.getenv("BR_CLOUD"))
# Public address of the app, used for the Google sign-in redirect.
PUBLIC_URL = (os.getenv("PUBLIC_URL") or os.getenv("RENDER_EXTERNAL_URL") or f"http://{HOST}:{PORT}").rstrip("/")
# Password for the login page. Required in cloud mode; optional on your own PC.
APP_PASSWORD = os.getenv("APP_PASSWORD", "")
# Random secret used to sign the login cookie and encrypt saved keys (set once, never change).
SECRET_KEY = os.getenv("BR_SECRET_KEY", "")
SESSION_DAYS = int(os.getenv("BR_SESSION_DAYS", "30"))

# Optional third-party keys (all optional; connectors that need them stay
# disabled with a clear message until configured).
ENV_KEYS = {
    "ADZUNA_APP_ID": "Adzuna app_id (free registration at developer.adzuna.com)",
    "ADZUNA_APP_KEY": "Adzuna app_key",
    "USAJOBS_API_KEY": "USAJOBS Authorization-Key (developer.usajobs.gov)",
    "USAJOBS_EMAIL": "Email used as USAJOBS User-Agent",
    "JOOBLE_API_KEY": "Jooble API key (jooble.org/api/about)",
    "THEMUSE_API_KEY": "Optional The Muse key for higher limits",
    "SMTP_HOST": "SMTP server for outreach (e.g. smtp.gmail.com)",
    "SMTP_PORT": "SMTP port (587 for STARTTLS)",
    "SMTP_USER": "SMTP username / email address",
    "SMTP_PASSWORD": "SMTP app password (never your main password)",
    "SMTP_FROM_NAME": "Display name on outgoing email",
    "GMAIL_CLIENT_ID": "Google OAuth client id (optional Gmail API sending)",
    "GMAIL_CLIENT_SECRET": "Google OAuth client secret",
}


def get_secret(name: str) -> str:
    """Environment first, then encrypted settings store."""
    val = os.getenv(name, "")
    if val:
        return val
    try:
        from .settings_store import get_secret_value

        return get_secret_value(name) or ""
    except Exception:
        return ""
