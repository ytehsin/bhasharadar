# भाषाRadar — Hindi language & AI-data job finder

A local app that searches free job APIs, company job boards and RSS feeds in one go, ranks every listing against **your resume**, hides what you can't apply for (wrong language, US-only, onsite abroad), and helps you apply and track applications. It runs on your computer; nothing about you is sent to any AI service.

## Use it online

To open it from your phone or any computer, follow **[DEPLOY.md](DEPLOY.md)**. It runs free on Render with a Neon database, behind your own password, and Gmail connects with Google sign-in.

## Start it on your PC (5 minutes)

You need **Python 3.10+** ([python.org](https://www.python.org/downloads/) — on Windows tick "Add Python to PATH").

**Windows:** double-click `run.bat`.
**macOS / Linux:** in a terminal, `./run.sh`

The first start installs dependencies (1–2 min). Then open **http://127.0.0.1:8000**.

Manual alternative:
```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --port 8000
```

## First use

1. **Settings → Upload resume** (PDF, DOCX or TXT). Check the extracted profile (years, languages, skills) and fix anything the parser missed. Until you upload, a *provisional* profile built from your project brief is used and clearly marked — emails drafted from it are blocked from sending.
2. **Sources → Check now** on each source (or run `python verify_sources.py`) to confirm which sources work from your connection.
3. **Jobs → GO.** Watch per-source progress; results appear ranked when done. You can also type `GO` or `STOP` in the command box.
4. Open a job to see **why** it scored as it did, what's missing, and prepare a cover letter, tailored resume (.docx) or form answers. **Apply** opens the official page; you confirm when you've actually submitted.
5. **Tracker** shows every saved/applied job; export to CSV or Excel.

The app does nothing on start-up — no searches, no emails — until you press GO (or turn on the optional daily search in Settings).

## What's included

| Area | What it does |
|---|---|
| Sources | 16 connectors behind one interface: Remotive, Himalayas, Jobicy, Remote OK, Arbeitnow, The Muse, Greenhouse/Lever/Ashby/Workable/SmartRecruiters company boards, Adzuna, Jooble, USAJOBS (keys), RSS/Atom, career pages (schema.org JobPosting). Add boards/feeds/pages in the UI. 20 sites without permitted automated access appear as one-click manual searches. See [SOURCES.md](SOURCES.md). |
| Search | Async, concurrency-limited, per-host rate limiting, retries with exponential backoff, response caching, per-source timeouts and isolation, saved sessions you can resume. |
| Matching | 100-point transparent score (skills 30 · experience 20 · responsibilities 15 · language & domain 10 · location 10 · seniority 5 · pay 5 · qualifications 5 — weights editable). TF-IDF similarity + rules; optional local multilingual embeddings. Hard blockers → **Not Eligible** with the reason (never silently dropped). Partial credit for closely related skills, "minor upskilling" flags. |
| Dedupe | Same job from two sources merged (shows "also posted on"); rediscovered jobs aren't counted as new; jobs that disappear from a company board are marked expired; "Check it's still open" per job. |
| Applications | Cover letters and tailored resumes built only from your resume. Form answers pre-filled; salary, notice period, work authorisation, relocation, declarations and screening questions are always left for you. Optional Playwright assistant fills contact fields on approved ATS domains and never submits. |
| Tracker | 11 statuses, history, follow-up dates, duplicate-application protection (including the same role via another source), CSV/Excel export. |
| Outreach | Draft only (default) → Review before sending → Automatic within rules. Recipients only from the posting itself. Daily/company caps, approved categories/recipient types/templates, min score, quiet hours, follow-up limits, blocklists, do-not-contact list, generic-message guard, auto-stop on delivery failures, emergency STOP. SMTP (app password) or Gmail OAuth. |
| Privacy | Local SQLite; API keys and tokens encrypted at rest; secrets never logged or returned to the browser; SSRF protection on user URLs; robots.txt respected for career pages; one-click deletion of resume and history. |

## Optional setup

**API keys** (Settings → API keys, or `.env` — see `.env.example`):
- Adzuna — free at developer.adzuna.com (adds India/UK search; 250 calls/day).
- Jooble — free key via jooble.org/api/about (aggregator incl. India).
- USAJOBS — only if you want US federal listings (most require US citizenship).

**Email** (Outreach page):
- *SMTP:* for Gmail, turn on 2-Step Verification and create an App Password, then set `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USER`, `SMTP_PASSWORD`. Click "Send test email to myself".
- *Gmail OAuth:* create an OAuth client (type "Web application") in Google Cloud Console, add redirect URI `http://127.0.0.1:8000/api/email/oauth/google/callback`, enable the Gmail API, paste client id/secret in Settings, then click **Connect Gmail**. Only the `gmail.send` scope is requested.

**Local embeddings:** `pip install sentence-transformers`, then tick "Use local embeddings" in Settings.
**Form assistant:** `pip install playwright && python -m playwright install chromium`.

## Tests

```bash
.venv/bin/python -m pytest -q
```
73 tests (run on both SQLite and Postgres) cover API failures, rate limits, duplicates, missing descriptions, expired links, location/remote classification, resume parsing failures, score logic, duplicate outreach, delivery failures, tracker updates, search cancellation and emergency STOP. Network calls are mocked with fixtures shaped like each API's documented response.

## Project layout

```
app/
  main.py                 FastAPI routes + serves the UI
  search_engine.py        GO lifecycle, progress, checkpoints, expiry
  connectors/             one class per source (common interface in base.py)
  job_source_registry.py  enable/disable, configs, health, manual-search links
  query_generator.py      queries from your profile, categories and past strong matches
  job_normalizer.py       common job format, dates, salary, remote/region classification
  deduplication_engine.py
  matching_engine.py      scoring, eligibility, explanations
  taxonomy.py             skills/tools/domains/languages vocabulary (edit to teach new terms)
  resume_parser.py, profile_store.py
  application_manager.py  cover letters, tailored resume, form answers, published contacts
  email_outreach.py, gmail_oauth.py, form_assist.py
  job_tracker.py, scheduler.py, audit_log.py, settings_store.py, security.py, http_client.py, db.py
  static/                 the dashboard (plain HTML/CSS/JS — no build step)
tests/                    pytest suite + API-shaped fixtures
verify_sources.py         live check of every source from your machine
```

**Adding a source in code:** subclass `BaseConnector` (see `connectors/public_apis.py`), fill in `meta`, implement `search()` and `normalize()`, register the class in `connectors/__init__.py` and add a row to `DEFAULT_SOURCES`. Nothing else changes.

See also [SOURCES.md](SOURCES.md) · [TROUBLESHOOTING.md](TROUBLESHOOTING.md) · [ROADMAP.md](ROADMAP.md).

*Not legal advice: you are responsible for using each source within its terms and for the emails you send.*
