# Troubleshooting

**`python` / `py` not found** — install Python 3.10+ from python.org; on Windows tick *Add Python to PATH*, then reopen the terminal.

**Port 8000 already in use** — `BR_PORT=8010 ./run.sh` (Windows: edit the port in `run.bat`). For Gmail OAuth also update the redirect URI to the new port.

**A source shows "Failing"** — open *Sources*, hover the error, and press *Check now*.
- `HTTP 403/406` or `Network error`: your network, VPN or firewall may block the host; try another network. Some providers block unusual User-Agents — set `BR_CONTACT_EMAIL` in `.env`.
- `HTTP 404` on a company board: the token is wrong or the company moved ATS. Open the company's careers page and look for `boards.greenhouse.io/<token>`, `jobs.lever.co/<site>` or `jobs.ashbyhq.com/<name>`.
- `Rate limited (HTTP 429)`: wait; the app already caches responses (Remotive 6 h, Jobicy 3 h, Himalayas 12 h). Don't press GO repeatedly.
- `Response was not valid JSON`: the provider changed its format. Disable the source and report it; other sources keep working.

**"Needs key"** — add the keys in *Settings → API keys* (or `.env`) and press *Check now*.

**Search finds few jobs** — normal for a niche: most free APIs are remote-tech heavy. Add Adzuna/Jooble keys (India-local roles), add company boards of localization/AI-data vendors you know, widen *Date posted*, clear quick filters, and click *Show not-eligible jobs* to see what was excluded and why. Use the manual-search links for LinkedIn/Naukri.

**A good job scored low** — open it and read *Missing or uncertain*. Usually a skill on the posting isn't on your profile: add it in *Settings → Your profile* (only if true), then *Re-score all jobs*. To teach new vocabulary edit `app/taxonomy.py`. Weights are adjustable in Settings.

**Resume upload fails** — scanned PDFs have no text; export as a text PDF or DOCX. Max 10 MB. If years of experience look wrong, correct them in the profile editor.

**Email test fails**
- Gmail SMTP `SMTPAuthenticationError`: you must use an *App Password* (requires 2-Step Verification), not your normal password.
- Gmail OAuth "no refresh token": remove the app's access at myaccount.google.com/permissions and connect again.
- Outlook/Office 365: `SMTP_HOST=smtp.office365.com`, port 587 (your organisation may block SMTP AUTH).

**Emails aren't sending** — check, in order: mode is not *Drafts only*; sending isn't STOPPED; the account is connected; the message isn't *blocked* (the reason is shown in the Messages table); quiet hours; daily limit. Drafts from the provisional profile are deliberately blocked — upload your resume and redraft.

**"You already applied"** — duplicate-application protection. Confirm the prompt to record another attempt on purpose.

**Form assistant does nothing** — install Playwright (`pip install playwright && python -m playwright install chromium`) and make sure the job's domain is in the approved list (default: Greenhouse, Lever, Ashby, Workable, SmartRecruiters).

**Start fresh** — *Settings → Privacy*, or stop the app and delete the `data/` folder (this also deletes your encrypted keys).

**Logs** — the terminal shows server output; *Settings → Privacy → Activity log* shows searches, sends and settings changes (secrets are never logged).
