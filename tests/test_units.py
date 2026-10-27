"""Unit tests: resume parsing, matching, normalisation, dedupe, security, HTTP retries."""
import asyncio

import httpx
import pytest
import respx

from app import job_normalizer as N
from app.deduplication_engine import dedupe_batch
from app.http_client import FetchError, HttpClient, RateLimited
from app.matching_engine import Matcher, quick_relevance
from app.models import JobRecord, Profile
from app.resume_parser import ResumeParseError, extract_text, parse_resume_text
from app.security import UnsafeURLError, validate_public_url
from tests.conftest import fx


def job(**kw):
    base = dict(source_id="t", source_name="Test", title="Hindi Linguist", company="Acme", description="")
    base.update(kw)
    return N.build(**base)


# ---------------------------------------------------------------- resume ----
def test_resume_extracts_real_facts_only():
    p = parse_resume_text(fx("sample_resume.txt"))
    assert p.name == "Priya Sharma"
    assert p.email == "priya.sharma@example.com"
    assert p.phone.replace(" ", "") == "+919876543210"
    assert {"language": "Hindi", "level": "Native"} in p.languages
    assert "Linguistic QA (LQA)" in p.skills and "Translation" in p.skills
    assert {"SDL/RWS Trados", "memoQ", "Xbench"} <= set(p.tools)
    assert p.total_years_experience and p.total_years_experience >= 7
    assert any(w.get("employer_guess") == "LinguaBridge Localization Pvt Ltd" for w in p.work_history)
    assert any(e["field_mass_comm"] for e in p.education)
    # Never invents: no Python/medical/etc.
    assert "Programming" not in " ".join(p.skills)
    assert p.verified is True


def test_resume_parse_failures():
    with pytest.raises(ResumeParseError):
        extract_text("cv.pdf", b"")
    with pytest.raises(ResumeParseError):
        extract_text("cv.exe", b"x" * 500)
    with pytest.raises(ResumeParseError):
        extract_text("cv.txt", b"too short")
    with pytest.raises(ResumeParseError):
        extract_text("cv.pdf", b"%PDF-broken" + b"0" * 200)


def test_docx_resume(tmp_path):
    import docx

    d = docx.Document()
    for line in fx("sample_resume.txt").split("\n"):
        d.add_paragraph(line)
    path = tmp_path / "cv.docx"
    d.save(path)
    text = extract_text("cv.docx", path.read_bytes())
    assert "Priya Sharma".upper() in text


# -------------------------------------------------------------- matching ----
@pytest.fixture
def matcher():
    text = fx("sample_resume.txt")
    return Matcher(parse_resume_text(text), text)


def test_title_alone_does_not_give_high_score(matcher):
    j = job(title="Hindi Translator", description="Must be a licensed attorney with LLB and bar admission. Python required.",
            location="Remote, India")
    m = matcher.score(j)
    assert m["score"] < 80
    assert any("Law" in x or "Programming" in x for x in m["missing"])


def test_non_language_role_capped(matcher):
    m = matcher.score(job(title="Account Executive", description="Sales quota, cold calling, SaaS.", location="Remote"))
    assert m["score"] <= 35 and not m["in_scope"]


def test_multilanguage_posting_listing_hindi_is_eligible(matcher):
    m = matcher.score(job(title="Linguists wanted", description="We need Hindi, Tamil and Telugu linguists. Native Tamil speaker or Hindi.",
                          location="India"))
    assert m["label"] != "Not Eligible"


def test_nice_to_have_is_not_missing(matcher):
    m = matcher.score(job(title="Hindi AI Trainer", description="Python experience is a plus. Evaluate AI responses in Hindi.",
                          location="Remote - Worldwide"))
    assert not any("Programming" in x for x in m["missing"])
    assert any("Programming" in x for x in m["uncertain"])


def test_citizenship_requirement_blocks(matcher):
    m = matcher.score(job(title="Hindi Language Analyst", description="Must be a U.S. citizen. Security clearance required. Hindi translation.",
                          location="Remote"))
    assert m["label"] == "Not Eligible"


def test_experience_gap_flagged():
    p = Profile(skills=["Translation"], languages=[{"language": "Hindi", "level": "Native"}], total_years_experience=1, verified=True)
    m = Matcher(p, "Hindi translation").score(job(title="Senior Hindi Translator", description="8+ years of translation experience required.",
                                                 location="India"))
    assert any("8+ years" in x for x in m["missing"])
    assert m["upskilling"] != "None"


def test_weights_configurable(matcher):
    j = job(title="Hindi Linguist", description="English to Hindi translation and proofreading", location="Remote, India")
    a = matcher.score(j)["score"]
    heavy_loc = Matcher(matcher.p, matcher.resume_text, {k: 0 for k in matcher.weights} | {"location": 100}).score(j)["score"]
    assert heavy_loc == 100.0 and a != heavy_loc


def test_quick_relevance():
    assert quick_relevance(job(title="Data Annotator", description="Hindi text labeling"))
    assert not quick_relevance(job(title="Backend Engineer", description="Go, Kubernetes"))


# ----------------------------------------------------------- normaliser ----
@pytest.mark.parametrize("loc,remote,regions", [
    ("Remote - Worldwide", "remote", ["Worldwide"]),
    ("Gurugram, India (Hybrid)", "hybrid", ["India"]),
    ("USA Only", "unknown", ["USA"]),
    ("Anywhere in the World", "remote", ["Worldwide"]),
    ("Berlin, Germany", "unknown", ["Europe"]),
])
def test_location_classification(loc, remote, regions):
    j = job(location=loc)
    assert j.remote_type == remote
    assert j.eligible_regions == regions


def test_dates_and_salary():
    assert N.to_iso(1700000000).startswith("2023-11-14")
    assert N.to_iso(1700000000000).startswith("2023-11-14")
    assert N.to_iso("Tue, 27 Oct 2026 10:00:00 +0000").startswith("2026-10-27")
    s = N.parse_salary_text("$15 - $25 per hour")
    assert (s["salary_min"], s["salary_max"], s["salary_currency"], s["salary_period"]) == (15, 25, "USD", "hour")
    assert N.parse_salary_text("₹40k–60k/month")["salary_currency"] == "INR"
    assert N.annual_usd(20, "USD", "hour") == 41600


def test_job_type():
    assert job(title="Hindi Linguist (Freelance)").job_type == "freelance"
    assert job(title="Hindi QA Tester (Part-Time)").job_type == "part_time"


# ---------------------------------------------------------------- dedupe ----
def test_dedupe_cross_source_and_same_source():
    a = job(source_id="a", source_job_id="1", title="Hindi Linguist (Remote)", company="LinguaWorks Inc.", location="India")
    b = job(source_id="b", source_job_id="9", title="Hindi Linguist", company="LinguaWorks", location="India")
    c = job(source_id="a", source_job_id="2", title="Hindi Linguist", company="LinguaWorks", location="India")  # different project, same source
    a_repeat = job(source_id="a", source_job_id="1", title="Hindi Linguist (Remote)", company="LinguaWorks Inc.", location="India")
    unique, dupes, cross, repeats = dedupe_batch([a, b, c, a_repeat])
    assert len(unique) == 2 and cross == 1 and repeats == 1


# -------------------------------------------------------------- security ----
@pytest.mark.parametrize("url", [
    "http://localhost/x", "http://127.0.0.1/", "http://169.254.169.254/latest/meta-data", "http://10.0.0.5/",
    "file:///etc/passwd", "http://user:pass@example.com/", "http://[::1]/", "http://example.com:22/", "ftp://example.com",
])
def test_ssrf_blocked(url):
    with pytest.raises(UnsafeURLError):
        validate_public_url(url, resolve=False)


def test_public_url_allowed():
    assert validate_public_url("https://example.com/careers", resolve=False)


# ------------------------------------------------------------------ http ----
def test_retry_then_success():
    async def go():
        with respx.mock() as m:
            route = m.get("https://api.example.com/x").mock(side_effect=[httpx.Response(503), httpx.Response(200, json={"ok": 1})])
            c = HttpClient(max_retries=2, backoff_base=0.01, cache_ttl=0, respect_intervals=False)
            data = await c.get_json("https://api.example.com/x")
            await c.aclose()
            return data, route.call_count

    data, calls = asyncio.run(go())
    assert data == {"ok": 1} and calls == 2


def test_rate_limit_long_retry_after_raises():
    async def go():
        with respx.mock() as m:
            m.get("https://api.example.com/y").mock(return_value=httpx.Response(429, headers={"Retry-After": "600"}))
            c = HttpClient(max_retries=3, backoff_base=0.01, cache_ttl=0, respect_intervals=False)
            try:
                await c.get_json("https://api.example.com/y")
            finally:
                await c.aclose()

    with pytest.raises(RateLimited):
        asyncio.run(go())


def test_404_not_retried():
    async def go():
        with respx.mock() as m:
            route = m.get("https://api.example.com/z").mock(return_value=httpx.Response(404))
            c = HttpClient(max_retries=3, backoff_base=0.01, cache_ttl=0, respect_intervals=False)
            try:
                await c.get_json("https://api.example.com/z")
            except FetchError as exc:
                return exc.status, route.call_count
            finally:
                await c.aclose()

    assert asyncio.run(go()) == (404, 1)


def test_cache_used():
    async def go():
        with respx.mock() as m:
            route = m.get("https://api.example.com/c").mock(return_value=httpx.Response(200, json=[1]))
            c = HttpClient(cache_ttl=60, respect_intervals=False)
            await c.get_json("https://api.example.com/c")
            await c.get_json("https://api.example.com/c")
            await c.aclose()
            return route.call_count

    assert asyncio.run(go()) == 1


def test_robots_respected():
    async def go():
        with respx.mock() as m:
            m.get("https://co.example/robots.txt").mock(return_value=httpx.Response(200, text="User-agent: *\nDisallow: /careers"))
            c = HttpClient(cache_ttl=0, respect_intervals=False)
            try:
                await c.get_text("https://co.example/careers", check_robots=True)
            finally:
                await c.aclose()

    with pytest.raises(FetchError, match="robots"):
        asyncio.run(go())


def test_career_page_jsonld():
    from app.connectors.feeds import detect_ats, extract_jobpostings

    html = """<html><script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting","title":"Hindi Editor",
      "hiringOrganization":{"name":"Acme"},"jobLocationType":"TELECOMMUTE","datePosted":"2026-10-20",
      "description":"<p>Edit Hindi copy</p>"}</script><iframe src="https://boards.greenhouse.io/embed/job_board?for=acmeco"></iframe></html>"""
    posts = extract_jobpostings(html)
    assert posts[0]["title"] == "Hindi Editor"
    assert detect_ats(html) == {"greenhouse": "acmeco"}


def test_other_target_language_not_eligible(matcher):
    for t in ("English to Latvian Review Lead for UI, Marketing & Product Content", "Georgian Linguist"):
        m = matcher.score(job(title=t, description="Review translations for UI and marketing content.", location="Remote - Worldwide"))
        assert m["label"] == "Not Eligible", t
