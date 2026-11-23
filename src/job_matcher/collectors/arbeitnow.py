"""Arbeitnow collector based on the official free job board API.

API: https://www.arbeitnow.com/api/job-board-api (no key, newest first, 300 jobs a page,
updated hourly). robots.txt allows everything; the terms (section 11) ask for a link back to
Arbeitnow, so the Arbeitnow job page is the vacancy URL. The API has no search or location
parameters, so the first page is read (about two days of postings, enough for a run every
3 hours) and filtered here: a QA-like title, and remote or in Poland.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

import httpx

from job_matcher.collectors.http import make_client
from job_matcher.models import RemoteType, Source, Vacancy
from job_matcher.normalize import (
    clean_company,
    detect_language,
    detect_seniority,
    html_to_text,
    requires_polish,
)

logger = logging.getLogger(__name__)

API_URL = "https://www.arbeitnow.com/api/job-board-api"

# Loose on purpose: the matcher decides what is a QA role; this only drops the bulk
# (marketing, sales, nursing…) before it reaches the funnel.
_QA_TITLE = re.compile(
    r"\b(QA|AQA|tester|test(?:ing)? engineer|testing|quality (?:assurance|engineer))\b",
    re.IGNORECASE,
)
_POLAND = re.compile(r"\b(Poland|Polska|Wroc[lł]aw|Warsaw|Warszawa|Krak[oó]w|Gda[nń]sk)\b", re.I)


def is_wanted(job: dict[str, Any]) -> bool:
    if not _QA_TITLE.search(job.get("title") or ""):
        return False
    return bool(job.get("remote")) or bool(_POLAND.search(job.get("location") or ""))


def parse_job(job: dict[str, Any]) -> Vacancy:
    description = html_to_text(job.get("description") or "")
    language = detect_language(description)
    title = " ".join(job["title"].split())
    location = job.get("location") or None
    return Vacancy(
        source=Source.ARBEITNOW,
        url=job["url"],
        title=title,
        company=clean_company(job["company_name"]),
        # "Remote job" is how the API says "no location": not a place.
        location=None if location == "Remote job" else location,
        remote=RemoteType.YES if job.get("remote") else None,
        seniority=detect_seniority(title),
        language_of_post=language,
        requires_polish=requires_polish(description, language),
        description=description,
        published_at=datetime.fromtimestamp(job["created_at"], tz=UTC),
    )


def parse_page(payload: dict[str, Any]) -> list[Vacancy]:
    """Wanted jobs of one page; a broken job is logged and skipped."""
    jobs = payload.get("data")
    if not isinstance(jobs, list):
        raise ValueError("Arbeitnow response has no 'data' list")
    vacancies = []
    for job in jobs:
        if not is_wanted(job):
            continue
        try:
            vacancies.append(parse_job(job))
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Arbeitnow: skipped job %r: %r", job.get("url"), exc)
    return vacancies


def fetch(client: httpx.Client) -> dict[str, Any]:
    response = client.get(API_URL, params={"page": 1})
    response.raise_for_status()
    return response.json()


def collect(client: httpx.Client) -> list[Vacancy]:
    try:
        return parse_page(fetch(client))
    except (httpx.HTTPError, ValueError) as exc:  # json.JSONDecodeError is a ValueError
        logger.warning("Arbeitnow: request failed: %s", exc)
        return []


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with make_client() as http:
        found = collect(http)
    for v in found:
        print(f"{v.published_at:%Y-%m-%d}  {v.title} @ {v.company}  | {v.location or '-'}")
    print(f"\n{len(found)} vacancies")
