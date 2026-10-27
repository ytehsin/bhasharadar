# Roadmap

Ordered by expected value for finding Hindi language / AI-data work, not by connector count.

## Next
1. **More vendor boards** — find and add ATS tokens for localization and AI-data vendors that hire Hindi talent (e.g. companies currently in the manual list that post via Greenhouse/Lever/Ashby). Use *Sources → Add a source → Career page* to auto-detect embedded boards.
2. **Himalayas free API key** — wire the key header once the exact header name is confirmed from their OpenAPI spec (higher, dedicated limits).
3. **Workday career sites** — many large vendors (Lionbridge, TransPerfect, Appen) use Workday; evaluate whether their public career-site JSON may be used under each company's terms before adding a connector.
4. **Reply detection** — read-only Gmail scope to detect replies/opt-outs automatically and pause follow-ups (currently you record outcomes in the Tracker and add opt-outs by hand).
5. **Local LLM drafting (opt-in)** — Ollama-based polishing of cover letters with a hard "facts only from resume" check; disabled unless you install it.

## Later
- Government portals: National Career Service (India) and state portals, if a permitted feed exists.
- University / research-lab career pages (IITs, IIITs, AI4Bharat-style projects) via the career-page connector.
- Salary normalisation with live FX rates (currently fixed approximate rates, labelled "approx.").
- Browser notifications for new Excellent matches after a scheduled search.
- Packaged desktop app (single-file installer) so Python isn't needed.
- Multi-profile support (e.g. separate Hindi and Urdu profiles).

## Done in v1.0
16 connectors, GO lifecycle with resumable sessions, transparent scoring with configurable weights, eligibility rules, dedupe & expiry, tracker with exports, three-mode outreach with STOP, Gmail OAuth/SMTP, encrypted secrets, 63 automated tests.
