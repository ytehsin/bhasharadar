"""End-to-end search pipeline against fixtures shaped like each real API."""
import asyncio
import json

import httpx

from app import db
from app.models import SearchFilters
from app.search_engine import SearchRun
from tests.conftest import fx, make_http


def run_search(filters=None, http=None):
    async def go():
        run = SearchRun(filters or SearchFilters(), http=http or make_http())
        await run.run()
        return run

    return asyncio.run(go())


def by_title(fragment):
    return db.query_one("SELECT * FROM jobs WHERE title LIKE ?", (f"%{fragment}%",))


def test_full_search_ranks_and_classifies(api_mock, resume_profile):
    run = run_search()
    assert run.status == "completed"
    assert run.stats["new"] == 11
    # Irrelevant listings (German accountant, software jobs) filtered before storage
    assert by_title("Buchhalter") is None
    assert by_title("Software Engineer, Infrastructure") is None
    top = db.query("SELECT title, match_label FROM jobs ORDER BY match_score DESC LIMIT 4")
    assert all("Hindi" in t["title"] for t in top)
    assert all(t["match_label"] in ("Excellent Match", "Strong Match") for t in top)


def test_language_mismatch_is_not_eligible(api_mock, resume_profile):
    run_search()
    ja = json.loads(by_title("Japanese Translator")["match_detail"])
    assert ja["label"] == "Not Eligible"
    assert "Japanese" in ja["hard_blockers"][0]
    odia = by_title("Oriya")
    assert odia["match_label"] == "Not Eligible"


def test_location_restrictions(api_mock, resume_profile):
    run_search()
    us = json.loads(by_title("Hindi AI Trainer")["match_detail"])  # USA only
    assert us["label"] == "Not Eligible"
    berlin = json.loads(by_title("Localization QA Tester (Hindi)")["match_detail"])  # onsite Berlin
    assert berlin["label"] == "Not Eligible"
    india = json.loads(by_title("Speech AI Evaluation")["match_detail"])
    assert india["eligibility"]["ok"] is True


def test_cross_source_duplicate_merged(api_mock, resume_profile):
    run = run_search()
    rows = db.query("SELECT * FROM jobs WHERE title LIKE 'Hindi Linguist%' AND title NOT LIKE '%QA%'")
    assert len(rows) == 1  # Remotive + Jobicy copies of the same LinguaWorks posting
    assert any("jobicy" in a["url"] for a in json.loads(rows[0]["also_on"]))
    assert run.stats["duplicates_removed"] >= 1


def test_rediscovered_not_counted_as_new(api_mock, resume_profile):
    run_search()
    first_seen = by_title("Speech AI")["first_seen_at"]
    run2 = run_search()
    assert run2.stats["new"] == 0
    assert run2.stats["rediscovered"] >= 10
    assert by_title("Speech AI")["first_seen_at"] == first_seen


def test_failed_source_does_not_stop_search(api_mock, resume_profile):
    api_mock["remotive"].mock(return_value=httpx.Response(500))
    run = run_search()
    assert run.status == "completed"
    assert run.sources["remotive"]["state"] == "error"
    assert run.sources["lever"]["state"] == "done"
    assert db.query_one("SELECT health FROM sources WHERE id='remotive'")["health"] == "error"


def test_rate_limit_reported(api_mock, resume_profile):
    api_mock["remoteok"].mock(return_value=httpx.Response(429, headers={"Retry-After": "120"}))
    run = run_search()
    assert run.sources["remoteok"]["state"] == "error"
    assert "429" in run.sources["remoteok"]["error"]
    assert db.query_one("SELECT health FROM sources WHERE id='remoteok'")["health"] == "rate_limited"


def test_missing_keys_skip_cleanly(api_mock, resume_profile):
    run = run_search()
    assert run.sources["adzuna"]["state"] == "skipped"
    assert "ADZUNA" in run.sources["adzuna"]["error"]


def test_expired_when_removed_from_complete_listing(api_mock, resume_profile):
    run_search()
    assert by_title("Speech AI")["status"] == "active"
    api_mock["lever_rws"].mock(return_value=httpx.Response(200, json=[]))
    run2 = run_search()
    assert by_title("Speech AI")["status"] == "expired"
    assert run2.stats["expired_marked"] >= 1


def test_old_postings_filtered_by_date(api_mock, resume_profile):
    data = fx("remotive.json")
    data["jobs"][0]["publication_date"] = "2020-01-01T00:00:00"
    api_mock["remotive"].mock(return_value=httpx.Response(200, json=data))
    api_mock["jobicy"].mock(return_value=httpx.Response(200, json={"jobs": []}))
    run_search(SearchFilters(posted_within_days=30))
    assert by_title("Hindi Linguist (Freelance)") is None


def test_search_cancellation(api_mock, resume_profile):
    async def go():
        run = SearchRun(SearchFilters(), http=make_http())
        run.cancel.set()
        await run.run()
        return run

    run = asyncio.run(go())
    assert run.status == "cancelled"
    assert db.query_one("SELECT COUNT(*) n FROM jobs")["n"] == 0
    assert db.query_one("SELECT status FROM search_sessions WHERE id=?", (run.id,))["status"] == "cancelled"


def test_missing_description_handled(api_mock, resume_profile):
    data = fx("lever_rws.json")
    data[0]["descriptionPlain"] = ""
    api_mock["lever_rws"].mock(return_value=httpx.Response(200, json=data))
    run_search()
    job = by_title("Speech AI")
    assert job is not None  # title alone is enough to keep it
    assert json.loads(job["match_detail"])["confidence"] == "Low"


def test_malformed_record_skipped(api_mock, resume_profile):
    data = fx("remotive.json")
    data["jobs"].append({"id": 999, "title": None, "description": 12345})
    data["jobs"].append("not-a-dict")
    api_mock["remotive"].mock(return_value=httpx.Response(200, json=data))
    run = run_search()
    assert run.status == "completed"
    assert run.sources["remotive"]["state"] == "done"
