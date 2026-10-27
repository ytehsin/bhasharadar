"""Optional Gmail sending via Google OAuth (scope: gmail.send only).

You create your own free OAuth client in Google Cloud Console (Desktop/Web app,
redirect URI http://127.0.0.1:8000/api/email/oauth/google/callback) and paste
the client id/secret in Settings. The refresh token is stored encrypted and is
never logged or shown.
"""
from __future__ import annotations

import secrets
import time
from urllib.parse import urlencode

import httpx

from . import config, db
from .audit_log import log
from .config import get_secret
from .settings_store import set_secret

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
SCOPES = "https://www.googleapis.com/auth/gmail.send openid email"
_cache: dict[str, float | str] = {}


def redirect_uri() -> str:
    return f"{config.PUBLIC_URL}/api/email/oauth/google/callback"


def start_url() -> str:
    cid = get_secret("GMAIL_CLIENT_ID")
    if not cid:
        raise RuntimeError("Add GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET in Settings first")
    state = secrets.token_urlsafe(24)
    db.set_setting("gmail_oauth_state", state)
    return AUTH_URL + "?" + urlencode({
        "client_id": cid, "redirect_uri": redirect_uri(), "response_type": "code", "scope": SCOPES,
        "access_type": "offline", "prompt": "consent", "state": state,
    })


async def handle_callback(code: str, state: str) -> str:
    if not state or state != db.get_setting("gmail_oauth_state"):
        raise RuntimeError("OAuth state mismatch — please start again")
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(TOKEN_URL, data={
            "code": code, "client_id": get_secret("GMAIL_CLIENT_ID"), "client_secret": get_secret("GMAIL_CLIENT_SECRET"),
            "redirect_uri": redirect_uri(), "grant_type": "authorization_code",
        })
        if r.status_code != 200:
            raise RuntimeError(f"Token exchange failed (HTTP {r.status_code})")
        tok = r.json()
        if not tok.get("refresh_token"):
            raise RuntimeError("Google did not return a refresh token — remove the app's access in your Google account and retry")
        set_secret("GMAIL_TOKEN_REFRESH", tok["refresh_token"])
        u = await c.get(USERINFO_URL, headers={"Authorization": f"Bearer {tok['access_token']}"})
        email = u.json().get("email", "") if u.status_code == 200 else ""
        if email:
            set_secret("GMAIL_TOKEN_EMAIL", email)
    _cache.update(token=tok["access_token"], exp=time.time() + int(tok.get("expires_in", 3000)) - 60)
    log("outreach", "gmail_connected", domain=email.split("@")[-1] if email else "")
    return email


async def access_token() -> str:
    if _cache.get("token") and float(_cache.get("exp", 0)) > time.time():
        return str(_cache["token"])
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(TOKEN_URL, data={
            "client_id": get_secret("GMAIL_CLIENT_ID"), "client_secret": get_secret("GMAIL_CLIENT_SECRET"),
            "refresh_token": get_secret("GMAIL_TOKEN_REFRESH"), "grant_type": "refresh_token",
        })
    if r.status_code != 200:
        raise RuntimeError(f"Could not refresh Gmail token (HTTP {r.status_code})")
    tok = r.json()
    _cache.update(token=tok["access_token"], exp=time.time() + int(tok.get("expires_in", 3000)) - 60)
    return tok["access_token"]


def disconnect() -> None:
    set_secret("GMAIL_TOKEN_REFRESH", "")
    set_secret("GMAIL_TOKEN_EMAIL", "")
    _cache.clear()
    log("outreach", "gmail_disconnected")
