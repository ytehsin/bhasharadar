"""Single-user login for the hosted version.

* One password (APP_PASSWORD). Compared in constant time.
* Signed, HttpOnly session cookie (HMAC-SHA256 with BR_SECRET_KEY), 30 days.
* Failed logins are rate-limited per IP (10 per 15 minutes).
* State-changing requests from other sites are refused (Origin check).
When APP_PASSWORD is empty on your own PC, the app stays open as before.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from urllib.parse import urlparse

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import config

COOKIE = "br_session"
_fails: dict[str, list[float]] = {}
OPEN_PATHS = {"/login", "/healthz", "/static/styles.css", "/favicon.ico"}  # query strings are ignored by path matching

_secret = (config.SECRET_KEY or (config.APP_PASSWORD and "pw:" + config.APP_PASSWORD) or secrets.token_hex(32)).encode()


def enabled() -> bool:
    return bool(config.APP_PASSWORD)


def _sign(payload: str) -> str:
    return hmac.new(_secret, payload.encode(), hashlib.sha256).hexdigest()


def make_token() -> str:
    exp = int(time.time()) + config.SESSION_DAYS * 86400
    payload = f"v1.{exp}"
    return f"{payload}.{_sign(payload)}"


def valid(token: str | None) -> bool:
    if not token or token.count(".") != 2:
        return False
    v, exp, sig = token.split(".")
    payload = f"{v}.{exp}"
    if not hmac.compare_digest(sig, _sign(payload)):
        return False
    try:
        return int(exp) > time.time()
    except ValueError:
        return False


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "?")


def too_many_attempts(ip: str) -> bool:
    now = time.time()
    recent = [t for t in _fails.get(ip, []) if now - t < 900]
    _fails[ip] = recent
    return len(recent) >= 10


def check_password(ip: str, password: str) -> bool:
    ok = hmac.compare_digest(password.encode(), config.APP_PASSWORD.encode())
    if not ok:
        _fails.setdefault(ip, []).append(time.time())
    else:
        _fails.pop(ip, None)
    return ok


def same_origin(request: Request) -> bool:
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        return True  # non-browser clients
    host = urlparse(origin).netloc
    return host in {request.url.netloc, urlparse(config.PUBLIC_URL).netloc}


async def middleware(request: Request, call_next):
    path = request.url.path
    if config.CLOUD and not enabled():
        if path == "/healthz":
            return await call_next(request)
        return HTMLResponse("<h3>Set the APP_PASSWORD environment variable to use BhashaRadar online.</h3>", status_code=503)
    if not enabled() or path in OPEN_PATHS:
        return await call_next(request)
    if request.method not in ("GET", "HEAD", "OPTIONS") and not same_origin(request):
        return JSONResponse({"detail": "Blocked: request came from another website"}, status_code=403)
    if valid(request.cookies.get(COOKIE)):
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"detail": "Please log in again"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


def set_cookie(resp, request: Request) -> None:
    resp.set_cookie(COOKIE, make_token(), max_age=config.SESSION_DAYS * 86400, httponly=True,
                    secure=request.url.scheme == "https" or config.PUBLIC_URL.startswith("https"), samesite="lax", path="/")


LOGIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Log in · BhashaRadar</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Courier+Prime:ital,wght@0,400;0,700;1,400&family=Noto+Serif+Devanagari:wght@400;600&display=swap" rel="stylesheet">
<script>(function(){var t="dark";try{t=localStorage.getItem("br-theme")||"dark"}catch(e){}document.documentElement.setAttribute("data-theme",t)})();</script>
<link rel="stylesheet" href="/static/styles.css"></head>
<body class="login-body"><main class="login-wrap">
<form class="search login-card" method="post" action="/login">
  <h1 class="brand"><span class="dev">भाषा</span>Radar<i class="spark" aria-hidden="true">.</i></h1>
  <p class="sub">Private job search. Enter your password to continue.</p>
  {error}
  <div class="field"><label for="pw">Password</label><input id="pw" name="password" type="password" autocomplete="current-password" autofocus required></div>
  <button class="go login-go" type="submit"><span>Enter</span></button>
</form></main></body></html>"""


def login_page(error: str = "") -> HTMLResponse:
    err = f'<div class="notice" role="alert">{error}</div>' if error else ""
    import os
    from pathlib import Path

    v = int(os.path.getmtime(Path(__file__).parent / "static" / "styles.css"))
    page = LOGIN_PAGE.replace("{error}", err).replace('/static/styles.css"', f'/static/styles.css?v={v}"')
    return HTMLResponse(page, status_code=401 if error else 200, headers={"Cache-Control": "no-cache"})
