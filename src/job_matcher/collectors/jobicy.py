"""Jobicy collector based on the official free remote-jobs API.

API docs: https://jobicy.com/jobs-rss-feed. No key; at most one request per second and a new
pass no more than once an hour (the bot runs every 3 hours and makes one request). Listings
must credit Jobicy and link to the Jobicy job page, so that page is the vacancy URL.
robots.txt allows /api/.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx

from job_matcher.collectors.http import make_client
from job_matcher.models import RemoteType, Seniority, Source, Vacancy
from job_matcher.normalize import (
    clean_company,
    detect_language,
    detect_seniority,
    html_to_text,
    requires_polish,
)

logger = logging.getLogger(__name__)

API_URL = "https://jobicy.com/api/jobs"
# The "tag" parameter also matches descriptions ("test" returns any engineering job); the
# QA & Testing industry is the precise filter. The API returns the last 7 days only.
SEARCH_PARAMS = {"geo": "europe", "industry": "qa-testing", "count": 100}

_SENIORITY = {
    "Junior": Seniority.JUNIOR,
    "Entry-Level": Seniority.JUNIOR,
    "Midweight": Seniority.MIDDLE,
    "Senior": Seniority.SENIOR,
    "Lead": Seniority.LEAD,
    "Manager": Seniority.LEAD,
    "Director": Seniority.LEAD,
}
# Monthly, like the other sources. Hourly rates can't be compared without hours.
_MONTHS_PER_PERIOD = {"monthly": 1, "yearly": 12, "annual": 12}


def _seniority(title: str, level: str | None) -> Seniority | None:
    """The title first; "Entry-Level, Junior" lists several levels, "Any" none."""
    if found := detect_seniority(title):
        return found
    mapped = {
        _SENIORITY[part.strip()] for part in (level or "").split(",") if part.strip() in _SENIORITY
    }
    return mapped.pop() if len(mapped) == 1 else None


def _salary(job: dict[str, Any]) -> tuple[int | None, int | None, str | None]:
    months = _MONTHS_PER_PERIOD.get(job.get("salaryPeriod") or "")
    low, high, currency = job.get("salaryMin"), job.get("salaryMax"), job.get("salaryCurrency")
    if not (months and currency and (low or high)):
        return None, None, None
    return (round(low / months) if low else None, round(high / months) if high else None,
            currency)  # fmt: skip


def parse_job(job: dict[str, Any]) -> Vacancy:
    description = html_to_text(job.get("jobDescription") or "")
    language = detect_language(description)
    salary_min, salary_max, currency = _salary(job)
    title = " ".join(job["jobTitle"].split())
    return Vacancy(
        source=Source.JOBICY,
        url=job["url"],
        title=title,
        company=clean_company(job["companyName"]),
        location=" ".join((job.get("jobGeo") or "").split()) or None,
        remote=RemoteType.YES,  # Jobicy lists remote jobs only; jobGeo says who may apply
        seniority=_seniority(title, job.get("jobLevel")),
        salary_min=salary_min,
        salary_max=salary_max,
        currency=currency,
        language_of_post=language,
        requires_polish=requires_polish(description, language),
        description=description,
        published_at=datetime.fromisoformat(job["pubDate"]),
    )


def parse_response(payload: dict[str, Any]) -> list[Vacancy]:
    """A broken job is logged and skipped, not fatal for the response."""
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        raise ValueError("Jobicy response has no 'jobs' list")
    vacancies = []
    for job in jobs:
        try:
            vacancies.append(parse_job(job))
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Jobicy: skipped job %r: %r", job.get("url"), exc)
    return vacancies


def fetch(client: httpx.Client) -> dict[str, Any]:
    response = client.get(API_URL, params=SEARCH_PARAMS)
    response.raise_for_status()
    return response.json()


def collect(client: httpx.Client) -> list[Vacancy]:
    try:
        return parse_response(fetch(client))
    except (httpx.HTTPError, ValueError) as exc:  # json.JSONDecodeError is a ValueError
        logger.warning("Jobicy: request failed: %s", exc)
        return []


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with make_client() as http:
        found = collect(http)
    for v in found:
        print(f"{v.published_at:%Y-%m-%d}  {v.seniority or '-':<6}  {v.title} @ {v.company}")
    print(f"\n{len(found)} vacancies")
