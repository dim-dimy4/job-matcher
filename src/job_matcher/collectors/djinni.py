"""Djinni collector: the official RSS feeds plus the structured data of each job page.

The RSS item has no company, location or salary. Each job page carries them as a
schema.org `JobPosting` (JSON-LD), the same data Djinni publishes for search engines, so
it is read instead of the page layout. robots.txt allows /jobs/; requests are paced.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any
from xml.etree.ElementTree import ParseError

import httpx
from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

from job_matcher.collectors.http import make_client
from job_matcher.models import RemoteType, Seniority, Source, Vacancy
from job_matcher.normalize import (
    clean_company,
    clean_url,
    detect_language,
    detect_seniority,
    html_to_text,
    requires_polish,
)

logger = logging.getLogger(__name__)

FEED_URL = "https://djinni.co/jobs/rss/"
# Remote roles needing 5+ years. "QA" is Djinni's manual-testing category.
FEED_KEYWORDS = ("QA Automation", "QA")
FEED_FILTERS = {"employment": "remote", "exp_level": "5y"}
MAX_JOB_PAGES = 30  # one request per vacancy: cap a run at 30 pages
DELAY_BETWEEN_REQUESTS_SECONDS = 2.0

SENIOR_MONTHS = 60  # Djinni's "5+ years": senior when the title names no grade

_MONTHS_PER_UNIT = {"MONTH": 1, "YEAR": 12}
_COUNTRIES = {
    "UA": "Ukraine", "UKR": "Ukraine", "PL": "Poland", "POL": "Poland",
    "RO": "Romania", "ROU": "Romania", "BG": "Bulgaria", "BGR": "Bulgaria",
    "MD": "Moldova", "MDA": "Moldova", "DE": "Germany", "DEU": "Germany",
    "CZ": "Czechia", "CZE": "Czechia", "PT": "Portugal", "PRT": "Portugal",
    "ES": "Spain", "ESP": "Spain", "US": "USA", "USA": "USA",
}  # fmt: skip
_JSON_LD = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.DOTALL)


@dataclass(frozen=True)
class FeedItem:
    url: str
    title: str
    published_at: datetime


def parse_feed(xml: bytes | str) -> list[FeedItem]:
    root = fromstring(xml)  # defusedxml: rejects entity-expansion and external-entity attacks
    items = []
    for item in root.iter("item"):
        url, title, date = item.findtext("link"), item.findtext("title"), item.findtext("pubDate")
        if not (url and title and date):
            logger.warning("Djinni: skipped feed item %r: missing link, title or date", url)
            continue
        items.append(FeedItem(clean_url(url), " ".join(title.split()), parsedate_to_datetime(date)))
    return items


def find_job_posting(page: str) -> dict[str, Any]:
    for block in _JSON_LD.findall(page):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            return data
    raise ValueError("no JobPosting JSON-LD on the page")


def _location(requirements: dict[str, Any] | list[dict[str, Any]] | None) -> str | None:
    areas = requirements if isinstance(requirements, list) else [requirements or {}]
    names = []
    for area in areas:
        address = area.get("address") or {}
        if region := address.get("addressRegion"):
            names.append(region)
        elif code := address.get("addressCountry"):
            names.append(_COUNTRIES.get(code, code))
    return ", ".join(names) or None


def _seniority(title: str, job: dict[str, Any]) -> Seniority | None:
    if from_title := detect_seniority(title):
        return from_title
    months = (job.get("experienceRequirements") or {}).get("monthsOfExperience") or 0
    return Seniority.SENIOR if months >= SENIOR_MONTHS else None


def _salary(job: dict[str, Any]) -> tuple[int | None, int | None, str | None]:
    """Monthly (min, max, currency); hourly or incomplete salaries are dropped."""
    salary = job.get("baseSalary") or {}
    value = salary.get("value") or {}
    months = _MONTHS_PER_UNIT.get(value.get("unitText"))
    low, high = value.get("minValue"), value.get("maxValue")
    if not (months and salary.get("currency") and (low or high)):
        return None, None, None
    monthly = [round(v / months) if v else None for v in (low, high)]
    return monthly[0], monthly[1], salary["currency"]


def parse_job(job: dict[str, Any], item: FeedItem) -> Vacancy:
    title = " ".join((job.get("title") or item.title).split())
    description = html_to_text(job.get("description", ""))
    language = detect_language(description)
    salary_min, salary_max, currency = _salary(job)
    return Vacancy(
        source=Source.DJINNI,
        url=item.url,
        title=title,
        company=clean_company(job["hiringOrganization"]["name"]),
        location=_location(job.get("applicantLocationRequirements")),
        remote=RemoteType.YES if job.get("jobLocationType") == "TELECOMMUTE" else None,
        seniority=_seniority(title, job),
        salary_min=salary_min,
        salary_max=salary_max,
        currency=currency,
        language_of_post=language,
        requires_polish=requires_polish(description, language),
        description=description,
        # The feed date has a timezone; JSON-LD's datePosted doesn't.
        published_at=item.published_at,
    )


def fetch(client: httpx.Client, url: str, params: dict[str, str] | None = None) -> str:
    response = client.get(url, params=params)
    response.raise_for_status()
    return response.text


def collect(
    client: httpx.Client, *, delay: float = DELAY_BETWEEN_REQUESTS_SECONDS
) -> list[Vacancy]:
    """Read the feeds, then one job page per vacancy. A failing request skips only its part."""
    items: dict[str, FeedItem] = {}
    for index, keyword in enumerate(FEED_KEYWORDS):
        if index:
            time.sleep(delay)
        try:
            feed = fetch(client, FEED_URL, {"primary_keyword": keyword, **FEED_FILTERS})
            batch = parse_feed(feed)
        except (httpx.HTTPError, ParseError, DefusedXmlException) as exc:
            logger.warning("Djinni: feed %r failed: %s", keyword, exc)
            continue
        for item in batch:
            items.setdefault(item.url, item)

    if len(items) > MAX_JOB_PAGES:
        logger.warning("Djinni: %d vacancies, reading the newest %d", len(items), MAX_JOB_PAGES)
    newest = sorted(items.values(), key=lambda i: i.published_at, reverse=True)[:MAX_JOB_PAGES]
    vacancies = []
    for item in newest:
        time.sleep(delay)
        try:
            vacancies.append(parse_job(find_job_posting(fetch(client, item.url)), item))
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            logger.warning("Djinni: skipped %s: %r", item.url, exc)
    return vacancies


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with make_client() as http:
        found = collect(http)
    for v in found:
        salary = f"{v.salary_min or '?'}-{v.salary_max or '?'} {v.currency}" if v.currency else ""
        print(
            f"{v.published_at:%Y-%m-%d}  {v.seniority or '-':<6}  {v.title} @ {v.company}"
            f"  | {v.location or '-'}  {salary}"
        )
    print(f"\n{len(found)} vacancies")
