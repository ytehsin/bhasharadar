import json
import os
import sys
from pathlib import Path

import httpx
import pytest
import respx

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Every test gets its own database, uploads dir and secret key."""
    monkeypatch.setenv("BR_SECRET_KEY_FILE", str(tmp_path / ".key"))
    for k in ("ADZUNA_APP_ID", "ADZUNA_APP_KEY", "JOOBLE_API_KEY", "SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    from app import config, db

    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path)
    pg = os.getenv("BR_TEST_DATABASE_URL")
    if pg:  # run the whole suite against Postgres:  BR_TEST_DATABASE_URL=postgresql://... pytest
        import psycopg

        with psycopg.connect(pg, autocommit=True) as c:
            c.execute("DROP SCHEMA public CASCADE")
            c.execute("CREATE SCHEMA public")
        db.set_database_url(pg)
    else:
        db.set_db_path(tmp_path / "test.db")
    db.migrate()
    from app.job_source_registry import seed_sources

    seed_sources()
    yield
    db.set_db_path(config.DB_PATH)


def fx(name: str):
    p = FIX / name
    return json.loads(p.read_text()) if p.suffix == ".json" else p.read_text()


@pytest.fixture
def api_mock():
    """Mocks every default source with fixtures shaped like the real APIs."""
    with respx.mock(assert_all_called=False) as m:
        m.get("https://remotive.com/api/remote-jobs", name="remotive").mock(return_value=httpx.Response(200, json=fx("remotive.json")))
        m.get(url__startswith="https://himalayas.app/jobs/api/search", name="himalayas").mock(return_value=httpx.Response(200, json=fx("himalayas.json")))
        m.get(url__startswith="https://jobicy.com/api/v2/remote-jobs", name="jobicy").mock(return_value=httpx.Response(200, json=fx("jobicy.json")))
        m.get("https://remoteok.com/api", name="remoteok").mock(return_value=httpx.Response(200, json=fx("remoteok.json")))
        m.get(url__startswith="https://www.arbeitnow.com/api/job-board-api", name="arbeitnow").mock(return_value=httpx.Response(200, json=fx("arbeitnow.json")))
        m.get(url__startswith="https://boards-api.greenhouse.io/v1/boards/agency/jobs", name="gh_agency").mock(return_value=httpx.Response(200, json=fx("gh_agency.json")))
        m.get(url__startswith="https://boards-api.greenhouse.io/v1/boards/scaleai/jobs", name="gh_scaleai").mock(return_value=httpx.Response(200, json=fx("gh_scaleai.json")))
        m.get(url__startswith="https://api.lever.co/v0/postings/weloglobal", name="lever_welo").mock(return_value=httpx.Response(200, json=fx("lever_weloglobal.json")))
        m.get(url__startswith="https://api.lever.co/v0/postings/rws", name="lever_rws").mock(return_value=httpx.Response(200, json=fx("lever_rws.json")))
        m.get("https://weworkremotely.com/remote-jobs.rss", name="wwr").mock(return_value=httpx.Response(200, text=fx("wwr.rss")))
        yield m


@pytest.fixture
def resume_profile():
    from app.profile_store import save_profile
    from app.resume_parser import parse_resume_text

    text = fx("sample_resume.txt")
    p = parse_resume_text(text)
    save_profile(p, source="resume", filename="sample_resume.txt", resume_text=text)
    return p


def make_http():
    from app.http_client import HttpClient

    return HttpClient(max_retries=1, backoff_base=0.01, cache_ttl=0, respect_intervals=False)
