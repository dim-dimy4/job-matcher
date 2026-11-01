"""Himalayas collector based on the official public Remote Jobs API.

API docs: https://himalayas.app/api. The API terms (https://himalayas.app/terms#api) allow
personal use without asking first; Apply must go through the `applicationLink`, so we keep
the Himalayas job page as the vacancy URL. Scraping the website itself is not allowed.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from job_matcher.collectors.http import make_client
from job_matcher.models import ContractType, RemoteType, Seniority, Source, Vacancy
from job_matcher.normalize import (
    clean_company,
    detect_language,
    detect_seniority,
    html_to_text,
    requires_polish,
)

logger = logging.getLogger(__name__)

SEARCH_URL = "https://himalayas.app/jobs/api/search"
# Jobs open to candidates in Poland, newest first. Runs every 3 h, so 2 pages (40 jobs)
# are enough to catch everything new without walking the whole result set.
SEARCH_PARAMS = {"q": "QA", "country": "PL", "sort": "recent"}
MAX_PAGES = 2
DELAY_BETWEEN_PAGES_SECONDS = 2.0
MAX_LISTED_COUNTRIES = 3

_SENIORITY = {
    "Entry-level": Seniority.JUNIOR,
    "Mid-level": Seniority.MIDDLE,
    "Senior": Seniority.SENIOR,
    "Manager": Seniority.LEAD,
    "Director": Seniority.LEAD,
    "Executive": Seniority.LEAD,
}
# Salaries are stored per month, like DOU's. Hourly rates can't be compared without hours.
_MONTHS_PER_PERIOD = {"monthly": 1, "annual": 12}


def _seniority(title: str, levels: list[str]) -> Seniority | None:
    """The title is the most specific signal; the API list often spans several levels."""
    if found := detect_seniority(title):
        return found
    mapped = {_SENIORITY[level] for level in levels if level in _SENIORITY}
    return mapped.pop() if len(mapped) == 1 else None


def _location(countries: list[str]) -> str:
    """'Worldwide' when unrestricted; long EU-wide lists are shortened for Telegram/Sheets."""
    if not countries:
        return "Worldwide"
    # The search is limited to Poland, so it is on the list; show it even in a long one.
    countries = sorted(countries, key=lambda c: c != "Poland")
    shown = ", ".join(countries[:MAX_LISTED_COUNTRIES])
    hidden = len(countries) - MAX_LISTED_COUNTRIES
    return f"{shown} +{hidden} more" if hidden > 0 else shown


def _monthly(amount: float | None, months: int) -> int | None:
    return None if amount is None else round(amount / months)


def parse_job(job: dict[str, Any]) -> Vacancy:
    description = html_to_text(job.get("description") or "")
    language = detect_language(description)

    salary_min = salary_max = currency = None
    months = _MONTHS_PER_PERIOD.get(job.get("salaryPeriod") or "")
    has_amount = job.get("minSalary") is not None or job.get("maxSalary") is not None
    if months and job.get("currency") and has_amount:
        salary_min = _monthly(job.get("minSalary"), months)
        salary_max = _monthly(job.get("maxSalary"), months)
        currency = job["currency"]

    return Vacancy(
        source=Source.HIMALAYAS,
        url=job["guid"],  # the job page on Himalayas; applicationLink is the same + utm
        title=job["title"],
        company=clean_company(job["companyName"]),
        location=_location(job.get("locationRestrictions") or []),
        remote=RemoteType.YES,  # Himalayas lists remote jobs only
        seniority=_seniority(job["title"], job.get("seniority") or []),
        salary_min=salary_min,
        salary_max=salary_max,
        currency=currency,
        contract_type=ContractType.B2B if job.get("employmentType") == "Contractor" else None,
        language_of_post=language,
        requires_polish=requires_polish(description, language),
        description=description,
        published_at=datetime.fromtimestamp(job["pubDate"], tz=UTC),
    )


def parse_page(payload: dict[str, Any]) -> list[Vacancy]:
    """Parse one search response. A broken job is logged and skipped, not fatal for the page."""
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        raise ValueError("Himalayas response has no 'jobs' list")
    vacancies = []
    for job in jobs:
        try:
            vacancies.append(parse_job(job))
        # KeyError/TypeError: field missing or of the wrong type; ValueError incl. pydantic's.
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Himalayas: skipped job %r: %r", job.get("guid"), exc)
    return vacancies


def fetch_page(client: httpx.Client, page: int) -> dict[str, Any]:
    response = client.get(SEARCH_URL, params={**SEARCH_PARAMS, "page": page})
    response.raise_for_status()
    return response.json()


def collect(client: httpx.Client, *, delay: float = DELAY_BETWEEN_PAGES_SECONDS) -> list[Vacancy]:
    """Fetch up to MAX_PAGES pages, stop at the first empty or failing one, dedupe by id."""
    vacancies: dict[str, Vacancy] = {}
    for page in range(1, MAX_PAGES + 1):
        if page > 1:
            time.sleep(delay)  # be polite: no bursts against the API
        try:
            batch = parse_page(fetch_page(client, page))
        except (httpx.HTTPError, ValueError) as exc:  # json.JSONDecodeError is a ValueError
            logger.warning("Himalayas: page %d failed: %s", page, exc)
            break
        if not batch:
            break
        for vacancy in batch:
            vacancies.setdefault(vacancy.id, vacancy)
    return list(vacancies.values())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with make_client() as http:
        found = collect(http)
    found.sort(key=lambda v: v.published_at or v.collected_at, reverse=True)
    for v in found:
        salary = f"{v.salary_min or '?'}-{v.salary_max or '?'} {v.currency}" if v.currency else ""
        print(
            f"{v.published_at:%Y-%m-%d}  {v.seniority or '-':<6}  {v.title} @ {v.company}"
            f"  | {v.location}  {salary}"
        )
    print(f"\n{len(found)} vacancies")
