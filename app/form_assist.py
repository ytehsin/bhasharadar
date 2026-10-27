"""Optional application-form assistant (Playwright).

Opens the verified official application page in a *visible* browser, fills only
basic contact fields from your parsed resume and attaches your resume file.
It never clicks Submit, never touches CAPTCHAs or login walls, and refuses any
domain that is not on the approved list. You review and submit yourself.

Requires:  pip install playwright && python -m playwright install chromium
"""
from __future__ import annotations

from typing import Any

from . import config, db
from .audit_log import log
from .profile_store import get_profile
from .security import domain_allowed, validate_public_url

DEFAULT_APPROVED_DOMAINS = [
    "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "smartrecruiters.com",
]

FIELD_HINTS = {
    "first_name": ["first name", "given name", "first_name"],
    "last_name": ["last name", "surname", "family name", "last_name"],
    "full_name": ["full name", "your name", "name"],
    "email": ["email", "e-mail"],
    "phone": ["phone", "mobile", "telephone"],
    "location": ["location", "city", "current location"],
}


def approved_domains() -> list[str]:
    return db.get_setting("form_assist_domains", DEFAULT_APPROVED_DOMAINS)


def available() -> bool:
    if config.CLOUD:  # no screen on a cloud server
        return False
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


async def open_and_prefill(url: str) -> dict[str, Any]:
    validate_public_url(url)
    if not domain_allowed(url, approved_domains()):
        raise PermissionError("This domain is not on your approved list for form assistance (Settings → Applications).")
    if not available():
        raise RuntimeError("Form assistance needs Playwright: pip install playwright && python -m playwright install chromium")
    from playwright.async_api import async_playwright

    p, _ = get_profile()
    if not p.verified:
        raise RuntimeError("Upload your resume first.")
    first, _, last = (p.name or "").partition(" ")
    values = {"first_name": first, "last_name": last, "full_name": p.name, "email": p.email, "phone": p.phone, "location": p.location}
    filled, needs_you = [], []
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=False)
    page = await browser.new_page()
    await page.goto(url, wait_until="domcontentloaded")
    for key, hints in FIELD_HINTS.items():
        if not values.get(key):
            continue
        for hint in hints:
            loc = page.get_by_label(hint, exact=False)
            try:
                if await loc.count() == 1 and await loc.is_editable():
                    if not await loc.input_value():
                        await loc.fill(values[key])
                        filled.append(key)
                    break
            except Exception:
                continue
    resume = next(iter(sorted(config.UPLOAD_DIR.glob("resume.*"))), None)
    if resume:
        try:
            inputs = page.locator("input[type=file]")
            if await inputs.count() >= 1:
                await inputs.first.set_input_files(str(resume))
                filled.append("resume_file")
        except Exception:
            needs_you.append("resume upload")
    needs_you += ["Salary / rate", "Notice period", "Work authorisation", "Screening questions", "Any declarations", "Final review and Submit"]
    log("applications", "form_assist_opened", domain=url.split("/")[2], filled=filled)
    # Browser intentionally left open for you to review and submit.
    return {"filled": filled, "needs_your_input": needs_you, "note": "Review every field, then submit yourself."}
