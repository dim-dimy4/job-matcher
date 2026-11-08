# Job Matcher

[![CI](https://github.com/dim-dimy4/job-matcher/actions/workflows/ci.yml/badge.svg)](https://github.com/dim-dimy4/job-matcher/actions/workflows/ci.yml)
[![Job search](https://github.com/dim-dimy4/job-matcher/actions/workflows/schedule.yml/badge.svg)](https://github.com/dim-dimy4/job-matcher/actions/workflows/schedule.yml)
[![Status buttons](https://github.com/dim-dimy4/job-matcher/actions/workflows/buttons.yml/badge.svg)](https://github.com/dim-dimy4/job-matcher/actions/workflows/buttons.yml)
[![Contract tests](https://github.com/dim-dimy4/job-matcher/actions/workflows/contract.yml/badge.svg)](https://github.com/dim-dimy4/job-matcher/actions/workflows/contract.yml)

Collects QA vacancies (DOU, Himalayas, Djinni), scores them against a candidate profile, sends the good ones to Telegram and logs them in a Google Sheet. Spec: [docs/SPEC.md](docs/SPEC.md).

> Status: MVP done (stage 6 of 6) — runs every 3 hours on GitHub Actions. v2: Djinni, status buttons in Telegram and a matcher calibrated on real verdicts are done; next is LLM scoring.

## Architecture

```mermaid
flowchart LR
    cron["GitHub Actions<br/>every 3 h"] --> main["main.py"]
    subgraph collectors
        dou["DOU<br/>RSS"]
        him["Himalayas<br/>JSON API"]
        djinni["Djinni<br/>RSS + JSON-LD"]
    end
    main --> dou & him & djinni
    dou & him & djinni -->|Vacancy| matcher["matcher<br/>rules from profile.yaml"]
    matcher -->|"score ≥ 70"| dedup{"already in<br/>the sheet?"}
    dedup -->|new| sheet[("Google Sheet<br/>Job Tracker")]
    sheet -->|"best 10"| tg["Telegram"]
    sheet -. "links, company + title" .-> dedup
    tg -. "Applied / Skipped<br/>every 15 min" .-> sheet
```

| Part | Module | Role |
|---|---|---|
| Collectors | [`collectors/`](src/job_matcher/collectors) | One module per board, official feeds/APIs only; each normalizes into the pydantic `Vacancy` model and skips its own failures, so one broken board doesn't stop the run |
| Matcher | [`matcher.py`](src/job_matcher/matcher.py) | Pure function `score(vacancy, profile)`: hard filters, then points, every point with a reason |
| Tracker | [`tracker/sheets.py`](src/job_matcher/tracker/sheets.py) | Appends rows and doubles as the dedup memory: a GitHub runner starts clean, so the sheet is the state that survives between runs |
| Notifier | [`notifier/telegram.py`](src/job_matcher/notifier/telegram.py) | One message per vacancy, at most 10 per run; written after the sheet, so a failed message is never resent |
| Buttons | [`notifier/buttons.py`](src/job_matcher/notifier/buttons.py) | Applied / Skipped under each message: presses are fetched from Telegram and set the Status in the sheet |
| Pipeline | [`main.py`](src/job_matcher/main.py) | Wiring, `--dry-run` and `--buttons-only`; outputs are passed in, so tests run it with fakes |

Workflows: [`ci.yml`](.github/workflows/ci.yml) runs ruff and pytest on every pull request and on `main` (required to merge); [`schedule.yml`](.github/workflows/schedule.yml) runs the search every 3 hours and on demand (*Run workflow*); [`buttons.yml`](.github/workflows/buttons.yml) applies the status buttons every 15 minutes; [`contract.yml`](.github/workflows/contract.yml) checks the live sources daily, separately, so a change on a job board doesn't block pull requests.

## Local setup

Requires Python 3.12+.

```bash
python -m venv .venv
.venv\Scripts\activate          # Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"

ruff check . && ruff format --check .
pytest --cov            # unit tests, offline
pytest -m contract      # live checks that the sources still return the expected shape
```

Print current QA vacancies from a source, or run the whole pipeline:

```bash
python -m job_matcher.collectors.dou
python -m job_matcher.collectors.himalayas

job-matcher --dry-run   # collect and score, print the matches; no secrets needed
job-matcher             # full run: new matches to the sheet, the best 10 to Telegram
job-matcher --buttons-only  # only apply the status buttons pressed in Telegram
```

## Sources

| Source | How | Notes |
|---|---|---|
| DOU.ua | Official RSS feeds (`category=QA`, `remote` and `relocation`) | 2 requests per run, deduplicated by URL; robots.txt allows `/vacancies/` |
| [Himalayas](https://himalayas.app) | Official public [Remote Jobs API](https://himalayas.app/api), `q=QA&country=PL` | Up to 2 pages per run; links point to the job on Himalayas, whose Apply button is the `applicationLink` required by the [API terms](https://himalayas.app/terms#api) |
| [Djinni](https://djinni.co) | Official RSS (`QA Automation` and `QA`, remote, 5+ years), then the schema.org `JobPosting` data of each job page for company, location and salary | Up to 2 feeds + 30 job pages per run, 2 s apart; robots.txt allows `/jobs/` |

JustJoin.it and No Fluff Jobs were considered and rejected: their robots.txt and terms forbid automated collection (details in [SPEC §4.1](docs/SPEC.md)).

## Matching

Rules from [SPEC §6](docs/SPEC.md), profile data in [config/profile.yaml](config/profile.yaml). The target is a hybrid web/API role: manual testing plus automation in Python.

- **Hard filters (score 0):** not a QA role; another stack, area or an SDET role in the title; junior; Polish above B1; minus keywords; on-site outside Wrocław; remote from Ukrainian cities only; more than 6 years of experience required; salary below the minimum.
- **Points:**

| Rule | Points |
|---|---|
| Middle or Senior (alike) / Lead | +20 / −15 |
| Hands-on testing: 3+ different phrases ("manual testing", "exploratory", "test cases"…) / 1–2 | +30 / +15 |
| Automation title (AQA, Automation) with fewer than 3 such phrases | −15 |
| Building frameworks ("from scratch", "test infrastructure"…), 2+ mentions | −20 |
| Mobile, load testing, data or hardware as the focus, 4+ mentions each | −25 |
| Java, C#, .NET, Kotlin or Swift in the post | −15 |
| Candidate's stack (Python, Pytest, Playwright, Selenium, Postman, REST/API, SQL) | +5 each, max +20 |
| Domain (SaaS, AI/LLM, payments, marketplace, EdTech) | +5 each, max +10 |
| Remote from Poland/EU/abroad, or Wrocław | +10 |
| Salary reaching the target | +10 |

Salary thresholds in `profile.yaml` are examples; the real ones are read from the `SALARY_MINIMUM` and `SALARY_TARGET` environment variables. Every score comes with its reasons, e.g.

```
85  Senior QA Automation Engineer @ FYST
    +20 seniority: senior; +30 hands-on testing: manual testing, manual and automated,
    manually testing; +15 stack: Python, Postman, API; +10 domain: payments, fintech;
    +10 remote: Poland
```

### Calibration

The weights were set against the candidate's own verdicts on the first 56 vacancies the bot sent (7 fit, 5 maybe, 38 no, 6 excluded because the post was gone):

| | Fit passed | No rejected |
|---|---|---|
| Before (role +30, seniority +20, keywords) | 6 / 7 | 6 / 38 (16%) |
| After (the rules above, threshold 60) | 6 / 7 | 37 / 38 (97%) |

The miss is a fit with no hands-on testing in the text at all, which rules can't tell from a pure automation role. The rules were tuned on the same 56 vacancies they are measured on, so 97% is optimistic; new vacancies are the real test. No rule names a company or a vacancy.

The dataset (full job posts and private verdicts) is kept in a git-ignored `data/labelled/` folder; a public repository shouldn't redistribute other people's job posts. With it in place:

```bash
python -m job_matcher.calibration   # every vacancy with its score and reasons, then the two numbers
pytest tests/unit/test_calibration.py   # fails if the rules fall below 6/7 fit or 90% no
```

In CI the real-data test is skipped; the loader and the report are tested on a tiny invented set.

## Outputs

New vacancies scoring at least `notify_threshold` (60) are appended to the "Job Tracker" Google Sheet, and the 10 best of them are sent to Telegram. The sheet is also the dedup memory: a vacancy whose link, or company + title, is already there is skipped, so a job posted on both boards arrives once.

One-time setup (secrets go in `.env`, template `.env.example`; it is git-ignored):

1. **Telegram.** Create a bot with [@BotFather](https://t.me/BotFather) → `TELEGRAM_BOT_TOKEN`. Send the bot any message, then open `https://api.telegram.org/bot<token>/getUpdates` and copy `chat.id` → `TELEGRAM_CHAT_ID`.
2. **Google Cloud.** Create a project, enable the *Google Sheets API*, create a service account and a JSON key for it. Put the key file next to the project as `*.service-account.json` (git-ignored) and set `GOOGLE_SERVICE_ACCOUNT_JSON` to its path; in GitHub Secrets put the JSON itself.
3. **Sheet.** Create an empty Google Sheet, share it with the service account's `client_email` as Editor, and copy the ID from its URL (`/spreadsheets/d/<ID>/edit`) → `SHEET_ID`. The bot writes the header on the first run; add the Status dropdown yourself (Data → Data validation). Columns are found by their header, so adding or moving columns by hand is safe; renaming or deleting one the bot writes (Date … Status, Applied on) stops the run with an error naming it.

### Status buttons

Each message has two buttons, **Applied** and **Skipped**. A press sets the vacancy's Status in the sheet; Applied also fills *Applied on* with the date, and Skipped clears it, so a mistaken press can be taken back. The pressed button gets a ✓.

The bot has no server waiting for presses: it runs in GitHub Actions. Telegram keeps presses for 24 hours, and [`buttons.yml`](.github/workflows/buttons.yml) fetches them every 15 minutes (`getUpdates`), so the status reaches the sheet some minutes after the press; every full run applies them too. Details:

- Only presses by the owner (`TELEGRAM_CHAT_ID`) count.
- A status set by hand further down the funnel (HR, Technical, Offer, Rejected…) is never overwritten by a late press.
- Presses are confirmed to Telegram only after the sheet is written; if the sheet fails, they come back next run. Setting the same status twice changes nothing, so a repeat is harmless.

The bot uses only the `spreadsheets` scope: it can edit the sheets shared with it and nothing else in Drive.

For the schedule, add the same six values as repository secrets (*Settings → Secrets and variables → Actions*): `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON` (the key JSON itself, not a path), `SHEET_ID`, `SALARY_MINIMUM`, `SALARY_TARGET`. GitHub masks secret values in the logs.

GitHub pauses scheduled workflows in a public repository after 60 days without commits; re-enable it on the Actions tab. Scheduled runs may also start several minutes late.
