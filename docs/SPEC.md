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

- Roles: Senior QA Engineer, QA Lead, QA Automation Engineer, AI/LLM QA, SDET
- Seniority: Senior, Lead (Middle+ allowed with low priority)
- Locations: Wrocław (hybrid/on-site), Remote (Poland, EU, UK)
- Languages: English C1; Polish B1 — filter out vacancies that require Polish C1/fluent
- Plus keywords: Playwright, Python, Pytest, API, Postman, LLM, AI, GenAI, Stripe, SaaS, startup
- Minus keywords: gambling, casino, iGaming, betting
- Salary: monthly thresholds in one base currency (PLN), USD/EUR converted by approximate rates: a hard minimum (below it: rejected) and a target (at or above it: bonus). The real values are private: `profile.yaml` holds examples, the real ones come from the `SALARY_MINIMUM` / `SALARY_TARGET` environment variables (GitHub Secrets, `.env`)

## 3. MVP scope

**In scope:**
- Sources: Himalayas and DOU (2 sources); see §4.1 for why not JustJoin.it
- Collection every 3 hours via GitHub Actions (cron)
- Normalization into a single vacancy model + deduplication by URL, and by company + title across boards
- Rule-based matching (no LLM), score 0–100
- Vacancies with score ≥ 70 sent to Telegram
- Matching vacancies appended to the "Job Tracker" Google Sheet
- Tests and CI

**Out of scope (later):**
- Djinni, more remote job boards
- LLM-based resume fit scoring (Claude API)
- Status buttons in Telegram
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

## 6. Matching logic (v1, rules)

- Hard filters (score 0 immediately): minus keywords; `requires_polish = true` at a level above B1; location outside the list (not remote and not in a profile city); salary given and its upper bound below the minimum (no salary in the post: not filtered); "Junior" in the title or junior seniority; no role keyword in the title (decided in stage 4: without it, a non-QA job with many keywords could reach 70)
- Points: role match (+30), Senior/Lead seniority (+20), plus keywords (+5 each, max +30), remote/Wrocław (+10), salary range given and its upper bound ≥ the target (+10). The contract type (B2B net vs UoP gross) is shown in the reason but not used for filtering yet. Reasons never print the thresholds
- `requires_polish`: the vacancy text language is pl, or the requirements mention "Polish C1 / fluent Polish / język polski"
- Every score comes with a list of reasons — for transparency and for tests

## 7. "Job Tracker" Google Sheet

Columns:
Date | Source | Company | Vacancy | Link | Location/format | Salary range | Score | Reasons | Status | Resume version | Applied on | Response on | HR | Test task | Tech | Rejection reason | Notes

Statuses (dropdown): New · Applied · HR · Test task · Technical · Offer · Rejected · Skipped

The bot only appends rows; I maintain statuses manually.

## 8. Telegram

Personal bot, one message per vacancy: title, company, location, salary range, score, 2–3 reasons, link. No spam: at most 10 messages per run (the highest scores), the rest goes only to the sheet. Errors never print the bot token: it is part of the API URL, and CI logs are public.

## 9. Testing (part of the portfolio)

- Unit: normalization, matching (table-driven cases via `pytest.mark.parametrize`), language detection and `requires_polish`
- Parsers: tests on saved fixtures (HTML/JSON responses), no network
- Contract tests: check that the source response still contains the required fields (run daily in `contract.yml`, separately from CI)
- Integration: Telegram and Sheets via mocks
- CI: ruff + pytest + coverage on every push; coverage report in the README

## 10. Repository layout

```
job-matcher/
  src/job_matcher/
    collectors/ (himalayas.py, dou.py)
    models.py  normalize.py  profile.py  matcher.py  settings.py
    notifier/telegram.py  tracker/sheets.py  main.py
  config/profile.yaml
  tests/ (unit/, fixtures/, contract/)
  .github/workflows/ (ci.yml, schedule.yml, contract.yml)
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
