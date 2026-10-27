# Job source integration matrix

Status as built on **27 Oct 2026**. "Verified" below means the endpoint, parameters, limits and terms were checked against the provider's documentation (or a reputable secondary write-up where noted). The build environment could not make live calls to these hosts, so **live behaviour must be confirmed on your machine** with `python verify_sources.py` or *Sources → Check now*. The app records the last successful sync and health of each source.

| Source | Type | Endpoint | Auth | Free tier / limits | Coverage | Filters used | Terms that matter | Default |
|---|---|---|---|---|---|---|---|---|
| **Remotive** | Public API | `remotive.com/api/remote-jobs` | none | ~4 calls/day max, >2/min blocked → app makes **one cached bulk call per 6 h** and filters locally | Remote, worldwide (many region-restricted) | none server-side | Link back to Remotive job URL & credit Remotive; listings delayed 24 h; don't redistribute to other job sites | On |
| **Himalayas** | Public API | `himalayas.app/jobs/api/search` | none (free key optional, not wired) | Shared public allowance; data refreshes daily → cached 12 h | Remote; `country=India` and `worldwide=true` queries | q, country, worldwide, page | Visible Himalayas credit; Apply must use `applicationLink`; no passing jobs to other boards | On |
| **Jobicy** | Public API | `jobicy.com/api/v2/remote-jobs` | none | Poll ≤ once/hour → cached 3 h; 1 broad + ≤5 tag calls | Remote, geo slugs | count, tag | Keep Jobicy as source & its URL; sanitise HTML | On |
| **Remote OK** | Public API | `remoteok.com/api` | none | Not published → 1 cached call / 6 h | Newest ~100 remote jobs, mostly tech | none | Hyperlink back to Remote OK and name it as source | On |
| **Arbeitnow** | Public API | `arbeitnow.com/api/job-board-api` | none | Not published → ≤5 pages, 2 s spacing | Mostly Germany/EU | page | Link to original posting | On |
| **The Muse** | Public API | `themuse.com/api/public/jobs` | none (optional key) | Per-hour limits | Mostly US; "Flexible / Remote" | page, location | Link to landing page | Off (field names from v2 docs; enable after Check now) |
| **Greenhouse boards** | ATS API | `boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` | none | No published GET limit → 1 s spacing, cached 3 h | Per company | — | Public Job Board API | On: `agency` (Meridial/Invisible AI trainer projects, incl. Hindi), `scaleai` |
| **Lever boards** | ATS API | `api.lever.co/v0/postings/{site}?mode=json` (EU host supported) | none | No published read limit | Per company | skip/limit | Public Postings API | On: `weloglobal` (Welocalize — Hindi linguist/LQA roles), `rws` (RWS TrainAI — Hindi speech/transcription/AI audit) |
| **Ashby boards** | ATS API | `api.ashbyhq.com/posting-api/job-board/{name}?includeCompensation=true` | none | No published limit | Per company | — | Public posting API | On, no boards yet — add yours |
| **Workable boards** | ATS feed | `workable.com/api/accounts/{slug}?details=true` | none | Not published | Per company | — | Community-documented career-widget feed, not an official API | On, no boards yet |
| **SmartRecruiters** | ATS API | `api.smartrecruiters.com/v1/companies/{id}/postings` | none | Per-IP limits | Companies with public feed enabled | limit/offset | Some companies return nothing | On, no boards yet |
| **Adzuna** | Key API | `api.adzuna.com/v1/api/jobs/{country}/search/{page}` | free app_id + app_key | 25/min, 250/day, 1,000/week, 2,500/month (commercial: 14-day trial) | Country-specific (default `in`, `gb`) | what, max_days_old | Attribute Adzuna; estimated salaries flagged | On, idle until keys added |
| **Jooble** | Key API | `POST jooble.org/api/{key}` | free key on request | Per key | ~70 countries incl. India | keywords, location | Per key agreement | On, idle until key added |
| **USAJOBS** | Key API | `data.usajobs.gov/api/search` | free key + email User-Agent | Free | US federal only | Keyword | Most roles need US citizenship (scored Not Eligible) | Off |
| **RSS / Atom** | Feed | your feeds | none | 2 s spacing, cached 3 h | Whatever you add | — | Use feeds published for syndication | On: We Work Remotely main feed |
| **Career pages** | HTML (JSON-LD) | your URLs | none | 3 s spacing, **robots.txt enforced**, SSRF-checked | Pages you add | — | Reads schema.org `JobPosting` only; reports embedded ATS boards | On, no pages yet |

Default company boards were chosen because live Hindi-relevant postings were found on them on 27 Oct 2026 (e.g. Welocalize "Hindi Linguistic QA Tester", RWS "Speech AI Evaluation Specialist – Hindi", Meridial "Hindi Language Specialist – Freelance AI Trainer").

## Not automated (manual-search links only)

These have no permitted automated access (login walls, anti-bot, or terms forbid scraping). The Sources page builds a search link for each from your query:

LinkedIn Jobs · Naukri · Indeed India · Foundit · ProZ.com · TranslatorsCafe · Localization Academy job board · Upwork · Wellfound · Mercor · micro1 · OpenTrain AI · DataAnnotation · Outlier · TELUS Digital AI Community · CrowdGen (Appen) · OneForma · Toloka · Lionbridge · TransPerfect.

Tip: when one of these companies posts through Greenhouse/Lever/Ashby/Workable, add that board token in *Sources → Add a source* and it becomes automated. Adding a career-page URL will detect embedded boards for you.

## Honest limits

- No service gives unlimited global coverage; Remotive/Jobicy/Himalayas are remote-focused and English-heavy, Adzuna/Jooble add India-local jobs only once you register keys.
- Many Indian portals (Naukri, Foundit, Indeed) offer no public API — those stay manual.
- Application submission APIs for Greenhouse/Lever require the *employer's* key, so the app never submits on your behalf; it opens the official page and records what you confirm.
