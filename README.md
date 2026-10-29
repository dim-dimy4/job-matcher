# Job Matcher

[![CI](https://github.com/dim-dimy4/job-matcher/actions/workflows/ci.yml/badge.svg)](https://github.com/dim-dimy4/job-matcher/actions/workflows/ci.yml)

Collects QA vacancies (DOU, Himalayas), scores them against a candidate profile, sends the good ones to Telegram and logs them in a Google Sheet. Spec: [docs/SPEC.md](docs/SPEC.md).

> Status: stage 3 of 6 — DOU and Himalayas collectors done; matcher, Telegram and Sheets next.

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

Print current QA vacancies from a source:

```bash
python -m job_matcher.collectors.dou
python -m job_matcher.collectors.himalayas
```

## Sources

| Source | How | Notes |
|---|---|---|
| DOU.ua | Official RSS feeds (`category=QA`, `remote` and `relocation`) | 2 requests per run, deduplicated by URL; robots.txt allows `/vacancies/` |
| [Himalayas](https://himalayas.app) | Official public [Remote Jobs API](https://himalayas.app/api), `q=QA&country=PL` | Up to 2 pages per run; links point to the job on Himalayas, whose Apply button is the `applicationLink` required by the [API terms](https://himalayas.app/terms#api) |

JustJoin.it and No Fluff Jobs were considered and rejected: their robots.txt and terms forbid automated collection (details in [SPEC §4.1](docs/SPEC.md)).

Secrets go in `.env` (template: `.env.example`); it is git-ignored.
