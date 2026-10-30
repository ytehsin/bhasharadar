"""Apply kit, saved answers, question mapping, quality flags, board discovery, new sources."""
import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import application_manager as AM
from app import db
from app import job_normalizer as N
from app.models import Profile
from tests.conftest import FIX, fx, make_http
from tests.test_pipeline import by_title, run_search

GH_QUESTIONS = {
    "title": "Hindi Language Specialist - Freelance AI Trainer Project",
    "questions": [
        {"label": "First Name", "required": True, "fields": [{"name": "first_name", "type": "input_text"}]},
        {"label": "Last Name", "required": True, "fields": [{"name": "last_name", "type": "input_text"}]},
        {"label": "Resume/CV", "required": True, "fields": [{"name": "resume", "type": "input_file"}]},
        {"label": "What country are you currently a resident of?", "required": True,
         "fields": [{"name": "question_1", "type": "multi_value_single_select", "values": [{"label": "Germany", "value": 1}, {"label": "India", "value": 2}]}]},
        {"label": "If you are currently based in United States, please confirm your state", "required": True,
         "fields": [{"name": "question_2", "type": "multi_value_single_select", "values": [{"label": "I am not located in the United States of America currently", "value": 0}, {"label": "AL", "value": 1}]}]},
        {"label": "LinkedIn Profile", "required": True, "fields": [{"name": "question_3", "type": "input_text"}]},
        {"label": "Please acknowledge that you have read and agree to our Privacy Policy.", "required": True,
         "fields": [{"name": "question_4[]", "type": "multi_value_multi_select", "values": [{"label": "Yes", "value": 1}]}]},
        {"label": "What is your expected hourly rate?", "required": False, "fields": [{"name": "question_5", "type": "input_text"}]},
        {"label": "Why are you interested in this role?", "required": False, "fields": [{"name": "question_6", "type": "textarea"}]},
    ],
}


def _profile(answers=None):
    p = Profile(name="Priya Sharma", email="p@example.com", phone="+91 98765 43210", location="Ghaziabad, India",
                total_years_experience=7, languages=[{"language": "Hindi", "level": "Native"}], verified=True,
                skills=["Translation", "Linguistic QA (LQA)"])
    p.preferences = {"answers": answers or {}}
    return p


def test_question_mapping_uses_profile_saved_answers_and_leaves_legal_to_you():
    p = _profile({"linkedin": "https://linkedin.com/in/priya", "country": "India", "rate_hourly": "USD 20/hour"})
    m = {q["label"]: AM.map_question(q, p, "Dear team,\n\nI have seven years of Hindi translation and LQA experience across fintech and AI data work.")
         for q in GH_QUESTIONS["questions"]}
    assert m["First Name"]["answer"] == "Priya" and m["Last Name"]["answer"] == "Sharma"
    assert m["Resume/CV"]["kind"] == "file"
    assert m["What country are you currently a resident of?"]["answer"] == "India"  # picks the real option label
    assert m["If you are currently based in United States, please confirm your state"]["answer"].startswith("I am not located")
    assert m["LinkedIn Profile"]["answer"] == "https://linkedin.com/in/priya" and m["LinkedIn Profile"]["kind"] == "saved"
    privacy = m["Please acknowledge that you have read and agree to our Privacy Policy."]
    assert privacy["kind"] == "you" and privacy["answer"] == ""  # never ticks legal boxes for you
    assert m["What is your expected hourly rate?"]["answer"] == "USD 20/hour"
    assert m["Why are you interested in this role?"]["kind"] == "draft"


def test_commitments_not_invented_without_saved_answers():
    p = _profile({})
    q = {"label": "What is your expected hourly rate?", "required": True, "fields": [{"name": "q", "type": "input_text"}]}
    out = AM.map_question(q, p, "")
    assert out["answer"] == "" and "Settings" in out["note"]


def test_apply_kit_endpoint_with_greenhouse_questions(api_mock, resume_profile):
    run_search()
    job = by_title("Freelance AI Trainer Project")
    api_mock["gh_agency"].mock(side_effect=lambda req: httpx.Response(
        200, json=GH_QUESTIONS if req.url.path.endswith("/jobs/4603936101") else fx("gh_agency.json")))
    from app.main import app

    with TestClient(app) as c:
        c.put("/api/answers", json={"linkedin": "https://linkedin.com/in/priya", "country": "India", "bogus": "x"})
        assert c.get("/api/answers").json()["values"] == {"linkedin": "https://linkedin.com/in/priya", "country": "India"}
        kit = c.get(f"/api/jobs/{job['id']}/apply-kit").json()
    assert kit["questions_source"] and len(kit["questions"]) == len(GH_QUESTIONS["questions"])
    assert {"label": "LinkedIn", "value": "https://linkedin.com/in/priya"} in kit["details"]
    assert any("Privacy" in n for n in kit["needs_you"])
    assert kit["job"]["apply_url"].startswith("https://job-boards.greenhouse.io/agency/jobs/")


def test_apply_kit_without_questions_and_email_apply(api_mock, resume_profile):
    run_search()
    job = by_title("Speech AI Evaluation")  # posting says: send your CV to the recruiting address
    kit = AM.apply_kit(job, _profile(), None)
    assert kit["questions"] == [] and kit["email_apply"]["email"] == "trainai.recruiting@rws-example.com"


def test_original_resume_download(api_mock):
    from app.main import app

    with TestClient(app) as c:
        assert c.get("/api/resume/file").status_code == 404
        data = (FIX / "sample_resume.txt").read_bytes()
        c.post("/api/resume", files={"file": ("cv.txt", data, "text/plain")})
        r = c.get("/api/resume/file")
        assert r.status_code == 200 and r.content == data and "Priya_Sharma_Resume.txt" in r.headers["content-disposition"]


# ---------------------------------------------------------------- quality ----
def test_scam_like_posting_flagged_and_capped(resume_profile):
    from app.matching_engine import Matcher

    m = Matcher(resume_profile, fx("sample_resume.txt"))
    j = N.build(source_id="t", source_name="T", title="Hindi Translator (Work from home)", company="QuickJobs",
                location="Remote, India", description="Hindi translation work. Pay a registration fee of Rs 999 to start. "
                "WhatsApp only: +91 90000 00000. Earn up to ₹5000 per day!")
    out = m.score(j)
    assert out["score"] <= 40 and len(out["caution"]) >= 2
    assert out["reasons"][0].startswith("Be careful")


def test_direct_badge_and_board_discovery(api_mock, resume_profile):
    run_search()
    from app.main import app

    # A strong match whose description points at an employer board we don't follow yet
    db.execute("UPDATE jobs SET description = description || ' Apply here: https://jobs.lever.co/newvendor/abc-123' "
               "WHERE title LIKE 'Hindi Language Specialist – AI Model%'")
    with TestClient(app) as c:
        items = c.get("/api/jobs").json()["items"]
        gh = next(i for i in items if i["source_id"] == "greenhouse")
        hima = next(i for i in items if i["source_id"] == "himalayas")
        assert gh["direct"] is True and hima["direct"] is False
        sugg = c.get("/api/sources/suggested").json()
        assert any(s["kind"] == "lever" and s["token"] == "newvendor" for s in sugg)
        assert not any(s["token"] in ("weloglobal", "rws", "agency") for s in sugg)  # already followed
        c.post("/api/sources/suggested", json={"kind": "lever", "token": "newvendor", "company": "New Vendor"})
        lever = next(s for s in c.get("/api/sources").json() if s["id"] == "lever")
        assert any(b["token"] == "newvendor" for b in lever["config"]["boards"])
        assert not any(s["token"] == "newvendor" for s in c.get("/api/sources/suggested").json())
        assert c.post("/api/sources/suggested", json={"kind": "lever", "token": "../etc"}).status_code == 422


# ---------------------------------------------------------------- sources ----
def test_greenhouse_large_board_fetches_only_likely_matches(resume_profile):
    import respx

    from app.connectors.ats import GreenhouseConnector
    from app.connectors.base import SearchContext
    from app.models import SearchFilters

    listing = {"jobs": [{"id": i, "title": f"Accountant {i}", "location": {"name": "US"}} for i in range(400)]
               + [{"id": 9001, "title": "AI Trainer - Advanced Hindi Fluency (India)", "location": {"name": "Remote"}},
                  {"id": 9002, "title": "Linguist reviewer", "location": {"name": "Remote"}}]}
    detail = {"id": 9001, "title": "AI Trainer - Advanced Hindi Fluency (India)", "absolute_url": "https://job-boards.greenhouse.io/big/jobs/9001",
              "location": {"name": "Remote"}, "content": "&lt;p&gt;Hindi AI training&lt;/p&gt;", "first_published": "2026-10-20T00:00:00Z"}

    async def go():
        with respx.mock(assert_all_called=False) as m:
            full = m.get("https://boards-api.greenhouse.io/v1/boards/big/jobs", params={"content": "true"}).mock(return_value=httpx.Response(500))
            m.get("https://boards-api.greenhouse.io/v1/boards/big/jobs").mock(return_value=httpx.Response(200, json=listing))
            d1 = m.get("https://boards-api.greenhouse.io/v1/boards/big/jobs/9001").mock(return_value=httpx.Response(200, json=detail))
            d2 = m.get("https://boards-api.greenhouse.io/v1/boards/big/jobs/9002").mock(return_value=httpx.Response(200, json={**detail, "id": 9002, "title": "Linguist reviewer"}))
            conn = GreenhouseConnector("greenhouse", config={"boards": [{"token": "big", "company": "Big"}]})
            http = make_http()
            jobs = await conn.search(SearchContext(http=http, queries=[], filters=SearchFilters()))
            await http.aclose()
            return jobs, d1.call_count, d2.call_count, full.call_count

    jobs, c1, c2, cfull = asyncio.run(go())
    assert [j.title for j in jobs][0].startswith("AI Trainer - Advanced Hindi")  # Hindi/India titles first
    assert c1 == 1 and c2 == 1 and cfull == 0 and len(jobs) == 2


def test_workable_widget_feed():
    from app.connectors.ats import WorkableConnector

    raw = {"title": "Language Data Quality Reviewer - Hindi", "shortcode": "ABC123", "telecommuting": True, "country": "India",
           "city": "", "state": "", "employment_type": "Contract", "url": "https://apply.workable.com/j/ABC123",
           "application_url": "https://apply.workable.com/j/ABC123/apply", "published_on": "2026-10-25",
           "description": "<p>Review Hindi language data.</p>", "_company": "Volga Partners"}
    j = WorkableConnector("workable").normalize(raw)
    assert j.remote_type == "remote" and "India" in j.eligible_regions and j.job_type == "contract"
    assert j.apply_url.endswith("/apply") and "Hindi" in j.description


def test_new_default_boards_merged_into_existing_install():
    from app.job_source_registry import seed_sources, update_source

    # Simulate an install created before v2 defaults, where the user removed one board
    db.execute("UPDATE sources SET config=? WHERE id='greenhouse'", (json.dumps({"boards": [{"token": "agency", "company": "A"}]}),))
    db.set_setting("source_defaults_version", 1)
    db.set_setting("removed_default_items", ["greenhouse:scaleai"])
    seed_sources()
    toks = [b["token"] for b in db.jload(db.query_one("SELECT config FROM sources WHERE id='greenhouse'")["config"])["boards"]]
    assert "prolificacademicltd" in toks and "agency" in toks and "scaleai" not in toks
    feeds = [f["url"] for f in db.jload(db.query_one("SELECT config FROM sources WHERE id='rss'")["config"])["feeds"]]
    assert "https://careers.sigma.ai/jobs.rss" in feeds
    # removing a board through the UI is remembered
    cfg = db.jload(db.query_one("SELECT config FROM sources WHERE id='lever'")["config"])
    update_source("lever", config={"boards": [b for b in cfg["boards"] if b["token"] != "appen"]})
    assert "lever:appen" in db.get_setting("removed_default_items")


def test_theme_and_versioned_assets_served():
    from app.main import app

    with TestClient(app) as c:
        html = c.get("/").text
        assert 'data-theme' in html and 'id="theme-toggle"' in html and "styles.css?v=" in html
        css = c.get("/static/styles.css").text
        assert ':root[data-theme="light"]' in css
