# Job Matcher

[![CI](https://github.com/dim-dimy4/job-matcher/actions/workflows/ci.yml/badge.svg)](https://github.com/dim-dimy4/job-matcher/actions/workflows/ci.yml)

Собирает QA-вакансии (DOU, JustJoin.it), оценивает их по профилю кандидата, присылает подходящие в Telegram и записывает в Google-таблицу. ТЗ: [docs/SPEC.md](docs/SPEC.md).

> Статус: этап 1 из 6 — каркас, модель `Vacancy`, CI.

## Локальный запуск

Нужен Python 3.12+.

```bash
python -m venv .venv
.venv\Scripts\activate          # Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"

ruff check . && ruff format --check .
pytest --cov
```

Секреты — в `.env` (шаблон: `.env.example`), в git не попадают.
