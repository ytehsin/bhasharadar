"""Login, session cookie, origin checks and cloud-mode behaviour."""
import pytest
from fastapi.testclient import TestClient

from app import auth, config, db


@pytest.fixture
def secured(monkeypatch):
    monkeypatch.setattr(config, "APP_PASSWORD", "correct horse")
    monkeypatch.setattr(auth, "_secret", b"test-secret")
    auth._fails.clear()
    from app.main import app

    with TestClient(app, base_url="https://bhasharadar.example") as c:
        yield c


def test_everything_locked_until_login(secured):
    c = secured
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert c.get("/api/status").status_code == 401
    assert c.get("/api/jobs").status_code == 401
    assert c.get("/healthz").status_code == 200
    assert c.get("/login").status_code == 200
    assert c.get("/static/styles.css").status_code == 200
    assert c.get("/static/app.js").status_code == 401 or c.get("/static/app.js", follow_redirects=False).status_code == 303


def test_wrong_then_right_password(secured):
    c = secured
    bad = c.post("/login", data={"password": "nope"}, follow_redirects=False)
    assert bad.status_code == 401 and "didn't match" in bad.text
    ok = c.post("/login", data={"password": "correct horse"}, follow_redirects=False)
    assert ok.status_code == 303
    cookie = ok.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "samesite=lax" in cookie.lower()
    assert c.get("/api/status").json()["auth"] is True
    c.post("/logout")
    assert c.get("/api/status").status_code == 401


def test_rate_limited(secured):
    for _ in range(10):
        secured.post("/login", data={"password": "x"})
    r = secured.post("/login", data={"password": "correct horse"})
    assert "Too many tries" in r.text


def test_tampered_or_expired_cookie_rejected(secured, monkeypatch):
    good = auth.make_token()
    v, exp, sig = good.split(".")
    assert auth.valid(good)
    assert not auth.valid(f"{v}.{int(exp) + 999}.{sig}")
    assert not auth.valid("garbage")
    monkeypatch.setattr(config, "SESSION_DAYS", -1)
    assert not auth.valid(auth.make_token())


def test_cross_site_post_blocked(secured):
    c = secured
    c.post("/login", data={"password": "correct horse"})
    r = c.post("/api/stop", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = c.post("/api/stop", headers={"Origin": "https://bhasharadar.example"})
    assert r.status_code == 200


def test_cloud_without_password_refuses(monkeypatch):
    monkeypatch.setattr(config, "CLOUD", True)
    monkeypatch.setattr(config, "APP_PASSWORD", "")
    from app.main import app

    with TestClient(app) as c:
        assert c.get("/").status_code == 503
        assert c.get("/api/jobs").status_code == 503
        assert c.get("/healthz").status_code == 200


def test_resume_file_kept_in_database(secured):
    from tests.conftest import FIX

    c = secured
    c.post("/login", data={"password": "correct horse"})
    data = (FIX / "sample_resume.txt").read_bytes()
    assert c.post("/api/resume", files={"file": ("cv.txt", data, "text/plain")}).status_code == 200
    assert db.get_file("resume") == ("cv.txt", data)
    from app.email_outreach import _resume_attachment

    assert _resume_attachment() == ("Resume.txt", data)
    c.delete("/api/resume")
    assert db.get_file("resume") is None


def test_public_url_used_for_google_redirect(monkeypatch):
    from app import gmail_oauth

    monkeypatch.setattr(config, "PUBLIC_URL", "https://bhasharadar.onrender.com")
    assert gmail_oauth.redirect_uri() == "https://bhasharadar.onrender.com/api/email/oauth/google/callback"


def test_any_secret_string_encrypts(monkeypatch):
    from app import settings_store

    monkeypatch.setenv("BR_SECRET_KEY", "just some random words from render")
    settings_store.set_secret("JOOBLE_API_KEY", "abc123")
    raw = db.query_one("SELECT value FROM secrets WHERE name='JOOBLE_API_KEY'")["value"]
    assert "abc123" not in raw and raw.startswith("fernet:")
    assert settings_store.get_secret_value("JOOBLE_API_KEY") == "abc123"
