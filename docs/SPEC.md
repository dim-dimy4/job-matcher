# Job Matcher — ТЗ первой версии (MVP)

Версия 1.0 · 29.10.2026 · Автор: Дмитрий Качук
Назначение документа: стартовая точка для сессии в Claude Code. Скопировать в репозиторий как `docs/SPEC.md`.

## 1. Цель

Тулза, которая регулярно собирает QA-вакансии с выбранных площадок, отбирает подходящие под мой профиль, присылает их в Telegram и ведёт учёт в Google-таблице.

Две цели одновременно:
1. **Практическая:** меньше ручного просмотра площадок, ни одной пропущенной релевантной вакансии.
2. **Портфолио:** публичный репозиторий, который подтверждает навыки автоматизации (Python, Pytest, Playwright, CI). Качество тестов и README так же важны, как сама функциональность.

## 2. Профиль кандидата (вход для матчинга)

Хранить в `config/profile.yaml`, не в коде.

- Роли: Senior QA Engineer, QA Lead, QA Automation Engineer, AI/LLM QA, SDET
- Грейд: Senior, Lead (Middle+ допускается с низким приоритетом)
- Локации: Wrocław (hybrid/on-site), Remote (Poland, EU, UK)
- Языки: English C1; Polish B1 — вакансии с обязательным Polish C1/fluent отсеивать
- Плюс-ключевые слова: Playwright, Python, Pytest, API, Postman, LLM, AI, GenAI, Stripe, SaaS, startup
- Минус-ключевые слова: gambling, casino, iGaming, betting
- Минимальная ставка: настраиваемое поле (B2B, в PLN или USD/месяц)

## 3. Объём MVP

**Входит:**
- Источники: JustJoin.it и DOU (2 штуки)
- Сбор каждые 3 часа через GitHub Actions (cron)
- Нормализация в единую модель вакансии + дедупликация по URL
- Матчинг по правилам (без LLM), оценка 0–100
- Отправка вакансий с оценкой ≥ 70 в Telegram
- Запись подходящих вакансий в Google-таблицу «Job Tracker»
- Тесты и CI

**Не входит (позже):**
- No Fluff Jobs, Djinni, remote-доски
- LLM-оценка соответствия резюме (Claude API)
- Кнопки статусов в Telegram
- Веб-дашборд
- LinkedIn — не парсить (нарушает правила площадки); только почтовые оповещения о вакансиях, вручную

## 4. Архитектура

```
GitHub Actions (cron, каждые 3 ч)
  → collectors/ (по одному модулю на источник)
  → normalize → Vacancy
  → storage (SQLite: seen URLs)  — дедупликация
  → matcher (правила из profile.yaml) → score + reasons
  → notifier/telegram  (score ≥ порога)
  → tracker/google_sheets (append row)
```

Правила сбора: редкие запросы, user-agent с контактом, уважать robots.txt и условия площадок, без обхода защит.

## 5. Модель данных

`Vacancy`:
- `id` (hash URL), `source`, `url`, `title`, `company`
- `location`, `remote` (yes/no/hybrid), `seniority`
- `salary_min`, `salary_max`, `currency`, `contract_type` (B2B/UoP)
- `language_of_post` (en/pl/uk), `requires_polish` (bool)
- `skills` (list), `published_at`, `collected_at`
- `score` (0–100), `match_reasons` (list строк)

## 6. Логика матчинга (v1, правила)

- Жёсткие фильтры (сразу 0): минус-слова; `requires_polish = true` при уровне выше B1; локация вне списка; Junior в названии
- Баллы: совпадение роли (+30), грейд Senior/Lead (+20), плюс-слова (+5 за каждое, максимум +30), remote/Wrocław (+10), вилка указана и ≥ минимума (+10)
- `requires_polish`: язык текста вакансии = pl, или в требованиях встречается «Polish C1 / fluent Polish / język polski»
- Каждая оценка сопровождается списком причин — для прозрачности и тестов

## 7. Google-таблица «Job Tracker»

Колонки:
Дата | Источник | Компания | Вакансия | Ссылка | Локация/формат | Вилка | Оценка | Причины | Статус | Версия резюме | Дата отклика | Дата ответа | HR | Test task | Tech | Причина отказа | Заметки

Статусы (выпадающий список): Новая · Откликнулся · HR · Тестовое · Техническое · Оффер · Отказ · Пропустил

Бот только добавляет строки; статусы веду я вручную.

## 8. Telegram

Личный бот, сообщение на вакансию: название, компания, локация, вилка, оценка, 2–3 причины, ссылка. Без спама: не больше N сообщений за запуск, остальное — в таблице.

## 9. Тестирование (часть портфолио)

- Unit: нормализация, матчинг (табличные кейсы через `pytest.mark.parametrize`), определение языка и `requires_polish`
- Парсеры: тесты на сохранённых фикстурах (HTML/JSON ответов), без сети
- Контрактные тесты: проверка, что ответ источника всё ещё содержит нужные поля (запуск по расписанию, отдельно)
- Интеграционные: Telegram и Sheets через моки
- CI: ruff + pytest + coverage на каждый push; отчёт о покрытии в README

## 10. Структура репозитория

```
job-matcher/
  src/job_matcher/
    collectors/ (justjoin.py, dou.py)
    models.py  normalize.py  matcher.py  storage.py
    notifier/telegram.py  tracker/sheets.py  main.py
  config/profile.yaml
  tests/ (unit/, fixtures/, contract/)
  .github/workflows/ (ci.yml, schedule.yml)
  docs/SPEC.md  README.md
```

## 11. Секреты

`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON`, `SHEET_ID` — только в GitHub Secrets и локальном `.env` (в `.gitignore`).

## 12. Этапы

1. Каркас репозитория, модель, CI, первый тест
2. Коллектор DOU + тесты на фикстурах
3. Коллектор JustJoin.it + тесты
4. Матчер по правилам + табличные тесты
5. Google-таблица + Telegram
6. Расписание в GitHub Actions, README с архитектурой
7. (v2) No Fluff Jobs, LLM-оценка, кнопки статусов

Ритм: 4–6 часов в неделю. Поиск работы вручную не ждёт тулзу.

## 13. Открытые вопросы

- Репозиторий публичный сразу или после этапа 4?
- Точные способы получения данных с JustJoin.it и DOU — проверить на этапах 2–3 (API/RSS/HTML)
- Есть ли на площадках фильтр по языку вакансии — проверить там же
- Минимальная ставка для фильтра

## 14. Стартовый промпт для Claude Code

> Прочитай docs/SPEC.md. Мы делаем MVP по этому ТЗ, этапами из раздела 12. Начни с этапа 1: создай каркас репозитория на Python 3.12 (src-layout, pyproject.toml, pytest, ruff), модель Vacancy, workflow CI в GitHub Actions и первый unit-тест. Объясняй решения коротко — это учебный проект для портфолио QA Automation.