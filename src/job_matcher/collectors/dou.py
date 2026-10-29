"""DOU.ua collector based on the official vacancy RSS feeds.

Network (`fetch_feed`) is kept separate from parsing (`parse_feed`), so all parsing
logic is tested offline against saved fixtures.
"""

from __future__ import annotations

import html
import logging
import re
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from xml.etree.ElementTree import Element, ParseError

import httpx
from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

from job_matcher.collectors.http import make_client
from job_matcher.models import RemoteType, Source, Vacancy
from job_matcher.normalize import (
    clean_url,
    detect_language,
    detect_seniority,
    html_to_text,
    requires_polish,
)

logger = logging.getLogger(__name__)

# remote: fully remote roles; relocation: roles abroad, incl. Poland. Overlap is deduplicated.
FEED_URLS = (
    "https://jobs.dou.ua/vacancies/feeds/?category=QA&remote",
    "https://jobs.dou.ua/vacancies/feeds/?category=QA&relocation",
)
DELAY_BETWEEN_FEEDS_SECONDS = 2.0

_REMOTE_MARKER = "віддалено"
# DOU writes ranges with an en dash ("$600\u20131000"); a plain hyphen is accepted too.
_SALARY = re.compile(r"^(?:(?P<prefix>від|до)\s*)?\$(?P<low>\d+)(?:\s*[\u2013-]\s*(?P<high>\d+))?$")


@dataclass(frozen=True)
class TitleParts:
    position: str
    company: str
    locations: tuple[str, ...] = ()
    remote: bool = False
    salary_min: int | None = None
    salary_max: int | None = None


def parse_title(title: str) -> TitleParts:
    """Split '<position> в <company>[, $salary][, locations...][, віддалено]'.

    The position may itself contain commas or ' в ', so we split on the *last* ' в '.
    """
    # DOU double-escapes entities in titles ("&amp;amp;", "від&amp;nbsp;$2700").
    title = html.unescape(title).replace("\xa0", " ").strip()
    position, sep, rest = title.rpartition(" в ")
    if not sep or not position:
        raise ValueError(f"unexpected DOU title format: {title!r}")

    company, *tail = (part.strip() for part in rest.split(","))
    locations: list[str] = []
    remote = False
    salary_min = salary_max = None
    for part in tail:
        if part == _REMOTE_MARKER:
            remote = True
        elif match := _SALARY.match(part):
            low = int(match["low"])
            high = int(match["high"]) if match["high"] else None
            if match["prefix"] == "до":
                salary_max = low
            elif match["prefix"] == "від":
                salary_min = low
            else:
                salary_min, salary_max = low, high if high is not None else low
        elif part:
            locations.append(part)

    return TitleParts(
        position=position.strip(),
        company=company,
        locations=tuple(locations),
        remote=remote,
        salary_min=salary_min,
        salary_max=salary_max,
    )


def _text(item: Element, tag: str) -> str:
    value = item.findtext(tag)
    if not value:
        raise ValueError(f"<item> has no <{tag}>")
    return value


def parse_item(item: Element) -> Vacancy:
    parts = parse_title(_text(item, "title"))
    description = html_to_text(_text(item, "description"))
    language = detect_language(description)
    has_salary = parts.salary_min is not None or parts.salary_max is not None
    return Vacancy(
        source=Source.DOU,
        url=clean_url(_text(item, "link")),
        title=parts.position,
        company=parts.company,
        location=", ".join(parts.locations) or None,
        # DOU only marks remote roles; absence of the marker doesn't prove on-site.
        remote=RemoteType.YES if parts.remote else None,
        seniority=detect_seniority(parts.position),
        salary_min=parts.salary_min,
        salary_max=parts.salary_max,
        currency="USD" if has_salary else None,
        language_of_post=language,
        requires_polish=requires_polish(description, language),
        published_at=parsedate_to_datetime(_text(item, "pubDate")),
    )


def parse_feed(xml: bytes | str) -> list[Vacancy]:
    """Parse one RSS document. A broken item is logged and skipped, not fatal for the feed."""
    root = fromstring(xml)  # defusedxml: rejects entity-expansion and external-entity attacks
    vacancies = []
    for item in root.iter("item"):
        try:
            vacancies.append(parse_item(item))
        except ValueError as exc:  # pydantic's ValidationError is a ValueError too
            logger.warning("DOU: skipped item %r: %s", item.findtext("link"), exc)
    return vacancies


def fetch_feed(client: httpx.Client, url: str) -> bytes:
    response = client.get(url)
    response.raise_for_status()
    return response.content


def collect(client: httpx.Client, *, delay: float = DELAY_BETWEEN_FEEDS_SECONDS) -> list[Vacancy]:
    """Fetch all feeds, deduplicate by vacancy id. One failing feed doesn't lose the others."""
    vacancies: dict[str, Vacancy] = {}
    for index, url in enumerate(FEED_URLS):
        if index:
            time.sleep(delay)  # be polite: no bursts against the site
        try:
            batch = parse_feed(fetch_feed(client, url))
        except (httpx.HTTPError, ParseError, DefusedXmlException) as exc:
            logger.warning("DOU: feed %s failed: %s", url, exc)
            continue
        for vacancy in batch:
            vacancies.setdefault(vacancy.id, vacancy)
    return list(vacancies.values())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with make_client() as http:
        found = collect(http)
    found.sort(key=lambda v: v.published_at or v.collected_at, reverse=True)
    for v in found:
        salary = f"${v.salary_min or '?'}-{v.salary_max or '?'}" if v.currency else ""
        print(
            f"{v.published_at:%Y-%m-%d}  {v.seniority or '-':<6}  {v.title} @ {v.company}"
            f"  | {v.location or '-'}{' | remote' if v.remote else ''}  {salary}"
        )
    print(f"\n{len(found)} vacancies")
