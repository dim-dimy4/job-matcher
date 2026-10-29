# Job Matcher

[![CI](https://github.com/dim-dimy4/job-matcher/actions/workflows/ci.yml/badge.svg)](https://github.com/dim-dimy4/job-matcher/actions/workflows/ci.yml)

Collects QA vacancies (DOU, JustJoin.it), scores them against a candidate profile, sends the good ones to Telegram and logs them in a Google Sheet. Spec: [docs/SPEC.md](docs/SPEC.md).

> Status: stage 2 of 6 — DOU collector done; JustJoin.it, matcher, Telegram and Sheets next.

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

Print current QA vacancies from DOU:

```bash
python -m job_matcher.collectors.dou
```

## Sources

| Source | How | Notes |
|---|---|---|
| DOU.ua | Official RSS feeds (`category=QA`, `remote` and `relocation`) | 2 requests per run, deduplicated by URL; robots.txt allows `/vacancies/` |

Secrets go in `.env` (template: `.env.example`); it is git-ignored.
