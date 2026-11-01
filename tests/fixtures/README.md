# Fixtures

Real source responses saved on 2026-10-29, then trimmed so the repository does not
redistribute job posts: 6 jobs per file, descriptions converted to plain text and cut to
1500 characters. Structure and field types are kept as the sources send them.

| File | Source | Request |
|---|---|---|
| `dou/qa_remote.xml` | DOU RSS | `https://jobs.dou.ua/vacancies/feeds/?category=QA&remote` |
| `dou/qa_relocation.xml` | DOU RSS | `https://jobs.dou.ua/vacancies/feeds/?category=QA&relocation` |
| `himalayas/qa_pl_page1.json`, `qa_pl_page2.json` | Himalayas API | `https://himalayas.app/jobs/api/search?q=QA&country=PL&sort=recent&page=N` |
| `djinni/rss_qa_automation.xml` | Djinni RSS (saved 2026-11-01) | `https://djinni.co/jobs/rss/?primary_keyword=QA+Automation&employment=remote&exp_level=5y` |
| `djinni/job_<id>.html` | Djinni job pages | the page of each feed item, reduced to its `JobPosting` JSON-LD block |

The jobs are picked to cover the parser cases the tests rely on (salary formats, HTML
entities, long country lists, Polish post, junior, non-QA noise) and 3 DOU jobs present
in both feeds (deduplication). The Djinni pages cover a salary with a region, a country
code, a list of countries and no location requirement. Contract tests (`pytest -m contract`) check that the live
responses still have this shape.
