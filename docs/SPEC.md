# Job Matcher — MVP Specification

Version 1.1 · 2026-10-29 · Author: Dmytro Kachuk
Purpose: starting point for a Claude Code session. Lives in the repository as `docs/SPEC.md`.

## 1. Goal

A tool that regularly collects QA vacancies from selected job boards, picks the ones that fit my profile, sends them to Telegram and keeps a log in a Google Sheet.

Two goals at once:
1. **Practical:** less manual browsing of job boards, no relevant vacancy missed.
2. **Portfolio:** a public repository that demonstrates automation skills (Python, Pytest, Playwright, CI). Test quality and the README matter as much as the functionality itself.

## 2. Candidate profile (matching input)

Stored in `config/profile.yaml`, not in code.

- Work: web and API testing, a hybrid of manual testing and automation in Python, test design, releases; a noticeable manual part is ideal (revised in v2 from the verdicts on the first 56 vacancies)
- Roles: QA Engineer, Senior QA Engineer, Full-stack QA, QA Engineer (Manual + Automation); not SDET / framework-writing roles
- Seniority: Middle, Middle/Senior and Senior alike; Lead only with a penalty (none of the fits was a lead); Junior filtered
- Experience required: up to 6 years
- Locations: Wrocław (hybrid/on-site), Remote (Poland, EU, UK); not remote-from-Ukraine-only
- Languages: English C1; Polish B1 — filter out vacancies that require Polish C1/fluent
- Stack: Python, pytest, requests, Playwright (Python), Selenium, Postman/REST API, SQL, Chrome DevTools, Jira
- Domains: web SaaS, AI/LLM products, payments, marketplaces, EdTech
- Not wanted as the main job: Java, C#, Go or JS/TS-only automation; native mobile (Appium); load testing; data/ETL; networks, hardware, embedded
- Minus keywords: gambling, casino, iGaming, betting
- Salary: monthly thresholds in one base currency (PLN), USD/EUR converted by approximate rates: a hard minimum (below it: rejected) and a target (at or above it: bonus). The real values are private: `profile.yaml` holds examples, the real ones come from the `SALARY_MINIMUM` / `SALARY_TARGET` environment variables (GitHub Secrets, `.env`)

## 3. MVP scope

**In scope:**
- Sources: Himalayas, DOU and Djinni (Djinni added in v2); see §4.1 for why not JustJoin.it
- Collection every 3 hours via GitHub Actions (cron)
- Normalization into a single vacancy model + deduplication by URL, and by company + title across boards
- Rule-based matching (no LLM), score 0–100
- Vacancies with score ≥ 70 sent to Telegram
- Matching vacancies appended to the "Job Tracker" Google Sheet
- Tests and CI

**Out of scope (later):**
- More remote job boards (We Work Remotely, Remote OK: allowed, little QA; see §4.1)
- LLM-based resume fit scoring (Claude API)
- Web dashboard
- LinkedIn — do not scrape (violates the platform's terms); only job alert emails, handled manually

## 4. Architecture

```
GitHub Actions (cron, every 3 h)
  → collectors/ (one module per source)
  → normalize → Vacancy
  → matcher (rules from profile.yaml) → score + reasons
  → dedup against the Google Sheet (URL, or company + title)
  → tracker/sheets (append rows, score ≥ threshold)
  → notifier/telegram (the best N of them)
```

Deduplication uses the sheet, not a local SQLite file (decided in stage 5): a GitHub Actions run starts on a clean machine, so a local file would be lost between runs, and the sheet already lists every vacancy the bot has sent. The sheet is written before Telegram: if a message fails, the vacancy is still logged and is not sent again on the next run.

Collection rules: infrequent requests, a user-agent with contact info, respect robots.txt and the platforms' terms, no bypassing of protections.

### 4.1 Sources (decided in stages 2–3)

| Source | Access | Status |
|---|---|---|
| DOU.ua | Official RSS feeds, QA category (`remote`, `relocation`) | Used |
| Himalayas | Official public JSON API (`/jobs/api/search`, `country=PL`); API terms allow personal use, Apply must use `applicationLink` | Used |
| Djinni | Official RSS (`primary_keyword=QA Automation` and `QA`, `employment=remote`, `exp_level=5y`) plus the schema.org `JobPosting` JSON-LD of each job page, which holds company, location and salary; robots.txt allows `/jobs/`, no published terms found; at most 30 pages per run, 2 s apart | Used (v2) |
| We Work Remotely | Public RSS, use allowed with attribution | Allowed; skipped for now: about 2 QA jobs at a time |
| Remote OK | Public API, use allowed with a link back | Allowed; skipped for now: no QA jobs among the latest 99 |
| Remotive | `/api/` disallowed in robots.txt | Not used |
| JustJoin.it | `/api/` disallowed in robots.txt; user terms §11.3 forbid automated data extraction without written consent | Not used; only job alert emails, handled manually |
| No Fluff Jobs | `/api/` and offer pages disallowed in robots.txt, CAPTCHA on the site | Not used |
| theprotocol.it, pracuj.pl | Behind bot protection (Cloudflare challenge) | Not used |

## 5. Data model

`Vacancy`:
- `id` (URL hash), `source`, `url`, `title`, `company`
- `location`, `remote` (yes/no/hybrid), `seniority`
- `salary_min`, `salary_max`, `currency`, `contract_type` (B2B/UoP)
- `language_of_post` (en/pl/uk), `requires_polish` (bool)
- `description` (plain text of the post, used for keyword matching; not stored in the sheet)
- `skills` (list: plus keywords found by the matcher), `published_at`, `collected_at`
- `score` (0–100), `match_reasons` (list of strings)

## 6. Matching logic (v2, rules calibrated on real verdicts)

v1 gave +30 for a QA title and +20 for seniority, so almost every vacancy started at 60 and keywords present in every post lifted it over 70: of the first 56 sent, the candidate marked 7 fit and 38 no, and the rules kept out only 6 of the 38. v2 scores what the job is, read from the description; the weights were checked against those verdicts (`python -m job_matcher.calibration`, README → Calibration).

- Hard filters (score 0 immediately): minus keywords; a title naming another stack, area or an SDET role (`title_minus_keywords`); `requires_polish = true` at a level above B1; location outside the list (not remote and not in a profile city); a remote post listing only Ukrainian cities (`local_only_places`; a Djinni post saying just "Ukraine" is kept: two fits were like that); more than `max_years_required` (6) years of experience asked; salary given and its upper bound below the minimum (no salary in the post: not filtered); "Junior" in the title or junior seniority; no role keyword in the title
- Points: Middle or Senior +20 (alike), Lead −15; hands-on testing: 3+ different phrases +30, 1–2 +15 (explicit phrases only: a bare "manual" is often negative, "reduce manual effort"); an automation title with fewer than 3 such phrases −15 (a title alone is not enough: one fit was "QA Automation" with 50/50 manual work); framework building 2+ mentions −20; mobile, load testing, data or hardware as the focus, 4+ mentions each −25; Java/C#/.NET/Kotlin/Swift in the post −15; the candidate's stack +5 each (max +20); domain +5 each (max +10); remote/Wrocław +10; salary reaching the target +10. The score is clamped to 0–100; threshold 60
- Remote earns +10 only when the location names Poland/EU/abroad/worldwide or a profile city (`remote_locations`); otherwise the reason says "check location". The contract type (B2B net vs UoP gross) is shown in the reason but not used for filtering yet. Reasons never print the thresholds
- Rules never name a company or a vacancy: they would fit the 56 and nothing else. What rules can't see (a fit with no hands-on testing in the text) is left for LLM scoring
- `requires_polish`: the vacancy text language is pl, or the requirements mention "Polish C1 / fluent Polish / język polski"
- Every score comes with a list of reasons — for transparency and for tests

## 7. "Job Tracker" Google Sheet

Columns:
Date | Source | Company | Vacancy | Link | Location/format | Salary range | Score | Reasons | Status | Comment | Resume version | Applied on | Response on | HR | Test task | Tech | Rejection reason | Notes

Columns are found by their header, not their position: adding or moving a column by hand breaks nothing (a "Comment" column inserted by hand once shifted "Applied on", and a button wrote the date under "Resume version"). A missing column the bot writes stops the run with an error naming it.

Statuses (dropdown): New · Applied · HR · Test task · Technical · Offer · Rejected · Skipped

The bot appends rows with status New. The Telegram buttons (§8) set Applied or Skipped; the later statuses I set by hand, and a button never overwrites them.

## 8. Telegram

Personal bot, one message per vacancy: title, company, location, salary range, score, 2–3 reasons, link. No spam: at most 10 messages per run (the highest scores), the rest goes only to the sheet. Errors never print the bot token: it is part of the API URL, and CI logs are public.

Status buttons (v2): **Applied** and **Skipped** under each message. The bot has no server, so presses are fetched with `getUpdates` by `buttons.yml` every 15 minutes (Telegram keeps them for 24 hours) and at the start of every full run; the status reaches the sheet minutes after the press, not at once. A webhook on a serverless function would be instant but adds a deploy, a second place for secrets and more to break; for a personal tracker a few minutes is fine. Button data is `<status>:<vacancy id>` (the id is a hash of the URL, within Telegram's 64-byte limit), and the row is found by the same hash of its link. Applied fills *Applied on*; Skipped clears it. Only the owner's presses count. Presses are confirmed after the sheet is written (at-least-once; writes are idempotent).

## 9. Testing (part of the portfolio)

- Unit: normalization, matching (table-driven cases via `pytest.mark.parametrize`), language detection and `requires_polish`
- Calibration: the real profile against the candidate's verdicts on 56 vacancies (fit passed, no rejected); the dataset is local only (`data/labelled/`, git-ignored), so in CI this test is skipped and the loader is tested on an invented set
- Parsers: tests on saved fixtures (HTML/JSON responses), no network
- Contract tests: check that the source response still contains the required fields (run daily in `contract.yml`, separately from CI)
- Integration: Telegram and Sheets via mocks
- CI: ruff + pytest + coverage on every push; coverage report in the README

## 10. Repository layout

```
job-matcher/
  src/job_matcher/
    collectors/ (himalayas.py, dou.py, djinni.py)
    models.py  normalize.py  profile.py  matcher.py  settings.py
    notifier/ (telegram.py, buttons.py)  tracker/sheets.py  main.py
  config/profile.yaml
  tests/ (unit/, fixtures/, contract/)
  .github/workflows/ (ci.yml, schedule.yml, buttons.yml, contract.yml)
  docs/SPEC.md  README.md
```

## 11. Secrets

`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON`, `SHEET_ID`, `SALARY_MINIMUM`, `SALARY_TARGET` — only in GitHub Secrets and a local `.env` (listed in `.gitignore`). `GOOGLE_SERVICE_ACCOUNT_JSON` holds the key JSON itself; in a local `.env` it may be a path to the key file instead.

## 12. Stages

1. Repository skeleton, model, CI, first test
2. DOU collector + fixture-based tests
3. Himalayas collector + tests (replaces JustJoin.it, see §4.1)
4. Rule-based matcher + table-driven tests
5. Google Sheet + Telegram (dedup via the sheet, see §4)
6. GitHub Actions schedule, README with architecture
7. (v2) More sources, LLM scoring, status buttons

Pace: 4–6 hours per week. The manual job search does not wait for the tool.

## 13. Open questions

- ~~Make the repository public right away or after stage 4?~~ — public after stage 4
- ~~Exact ways to get data from the sources~~ — decided, see §4.1
- Do the platforms offer a filter by vacancy language — check at the same time
- ~~Minimum rate for the filter~~ — decided, private values in `SALARY_MINIMUM` / `SALARY_TARGET` (§2)

## 14. Starting prompt for Claude Code

> Read docs/SPEC.md. We are building the MVP from this spec, in the stages from section 12. Start with stage 1: create a repository skeleton on Python 3.12 (src-layout, pyproject.toml, pytest, ruff), the Vacancy model, a GitHub Actions CI workflow and the first unit test. Explain decisions briefly — this is a learning project for a QA Automation portfolio.
