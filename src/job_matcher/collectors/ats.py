"""Company job boards on applicant tracking systems: Greenhouse, Lever, Ashby, Recruitee.

Each ATS publishes a company's open jobs through a public, keyless API meant for career
sites; the companies to read are listed in config/companies.yaml. Checked 2026-11-23:
- Greenhouse Job Board API: GET needs no auth; robots.txt only closes /embed/.
- Lever Postings API: "may be scraped by third parties"; robots.txt Crawl-delay: 1.
- Ashby Posting API: no key, no stated limits; api.ashbyhq.com serves no robots.txt.
- Recruitee Careers Site API: no auth; each company's robots.txt only closes /v/.
SmartRecruiters is not supported: its API host's robots.txt disallows everyone but LinkedIn.

Jobs are kept when the title looks like QA and the job is open to someone in Poland: the
location names Poland or a Polish city, or the job is remote without a narrower region than
Europe. Requests are 1 s apart (Lever's crawl delay, applied to all).
"""

from __future__ import annotations

import html
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml
from pydantic import BaseModel, ConfigDict

from job_matcher.collectors.http import make_client
from job_matcher.models import RemoteType, Source, Vacancy
from job_matcher.normalize import (
    detect_language,
    detect_seniority,
    html_to_text,
    looks_like_qa,
    requires_polish,
)

logger = logging.getLogger(__name__)

DEFAULT_COMPANIES_PATH = Path("config") / "companies.yaml"
DELAY_BETWEEN_REQUESTS_SECONDS = 1.0

_POLAND = re.compile(
    r"\b(Poland|Polska|Wroc[lł]aw|Warsaw|Warszawa|Krak[oó]w|Gda[nń]sk|Pozna[nń]|[ŁL][oó]d[zź]"
    r"|Katowice|Gliwice|Szczecin|Lublin)\b",
    re.IGNORECASE,
)
# A remote job limited to one of these (or to nothing) is open to someone in Poland.
_WIDE_REGION_WORDS = re.compile(
    r"\b(Europe|European Union|EU|EMEA|CET|Worldwide|Anywhere|Global|Remote|in|only)\b", re.I
)


class Company(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    ats: Source
    slug: str


def load_companies(path: Path = DEFAULT_COMPANIES_PATH) -> list[Company]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [Company.model_validate(item) for item in data["companies"]]


@dataclass(frozen=True)
class Job:
    """What every ATS answers, before the filter decides whether it becomes a Vacancy."""

    title: str
    url: str
    location: str  # free text, may list several places
    remote: RemoteType | None  # as the ATS states it; None when it doesn't
    description_html: str
    published_at: datetime | None

    @property
    def work_format(self) -> RemoteType | None:
        """The stated format, else "Remote" written in the location (Greenhouse has no field
        for it, and Ashby boards often leave theirs empty)."""
        if self.remote is None and re.search(r"\bremote\b", self.location, re.IGNORECASE):
            return RemoteType.YES
        return self.remote


def open_to_candidate(job: Job) -> bool:
    if _POLAND.search(job.location):
        return True
    if job.work_format is not RemoteType.YES:
        return False
    # Remote: fine when nothing narrower than a wide region is named ("Remote - US" is not).
    return not _WIDE_REGION_WORDS.sub(" ", re.sub(r"[^\w]+", " ", job.location)).strip()


_WORKPLACE = {"remote": RemoteType.YES, "hybrid": RemoteType.HYBRID, "onsite": RemoteType.NO}


def _workplace(value: str | None) -> RemoteType | None:
    """Lever "remote" / "hybrid" / "onsite", Ashby "Remote" / "Hybrid" / "OnSite"."""
    return _WORKPLACE.get((value or "").replace("-", "").replace("_", "").casefold())


def _greenhouse(payload: dict[str, Any]) -> list[Job]:
    jobs = []
    for job in payload["jobs"]:
        location = (job.get("location") or {}).get("name") or ""
        updated = job.get("first_published") or job.get("updated_at")
        jobs.append(Job(
            title=job["title"],
            url=job["absolute_url"],
            location=location,
            remote=None,  # no field for it: Job.work_format reads the location
            # Greenhouse escapes the HTML of the description once more.
            description_html=html.unescape(job.get("content") or ""),
            published_at=datetime.fromisoformat(updated) if updated else None,
        ))  # fmt: skip
    return jobs


def _lever(payload: list[dict[str, Any]]) -> list[Job]:
    jobs = []
    for job in payload:
        categories = job.get("categories") or {}
        places = categories.get("allLocations") or [categories.get("location") or ""]
        if job.get("country"):
            places = [*places, job["country"]]
        lists = "".join(f"<h3>{item.get('text', '')}</h3>{item.get('content', '')}"
                        for item in job.get("lists") or [])  # fmt: skip
        created = job.get("createdAt")  # milliseconds; not in the documented field table
        jobs.append(Job(
            title=job["text"],
            url=job["hostedUrl"],
            location=", ".join(p for p in places if p).replace("PL", "Poland"),
            remote=_workplace(job.get("workplaceType")),
            description_html=f"{job.get('description', '')}{lists}{job.get('additional', '')}",
            published_at=datetime.fromtimestamp(created / 1000, tz=UTC) if created else None,
        ))  # fmt: skip
    return jobs


def _ashby(payload: dict[str, Any]) -> list[Job]:
    jobs = []
    for job in payload["jobs"]:
        if job.get("isListed") is False:  # reachable by direct link only
            continue
        address = (job.get("address") or {}).get("postalAddress") or {}
        places = [
            job.get("location") or "",
            address.get("addressLocality") or "",
            address.get("addressCountry") or "",
            *((s.get("location") or "") for s in job.get("secondaryLocations") or []),
        ]
        published = job.get("publishedAt")
        jobs.append(Job(
            title=job["title"],
            url=job["jobUrl"],
            location=", ".join(dict.fromkeys(p for p in places if p)),
            remote=_workplace(job.get("workplaceType"))
            or (RemoteType.YES if job.get("isRemote") else None),
            description_html=job.get("descriptionHtml") or "",
            published_at=datetime.fromisoformat(published) if published else None,
        ))  # fmt: skip
    return jobs


def _recruitee(payload: dict[str, Any]) -> list[Job]:
    jobs = []
    for job in payload["offers"]:
        places = [", ".join(p for p in (loc.get("city"), loc.get("country")) if p)
                  for loc in job.get("locations") or []]  # fmt: skip
        if job.get("remote"):
            remote = RemoteType.YES
        elif job.get("hybrid"):
            remote = RemoteType.HYBRID
        else:
            remote = RemoteType.NO if job.get("on_site") else None
        published = job.get("published_at") or job.get("created_at")
        jobs.append(Job(
            title=job["title"],
            url=job["careers_url"],
            location="; ".join(p for p in places if p) or (job.get("location") or ""),
            remote=remote,
            description_html=f"{job.get('description', '')}{job.get('requirements', '')}",
            published_at=datetime.strptime(published, "%Y-%m-%d %H:%M:%S %Z").replace(tzinfo=UTC)
            if published else None,
        ))  # fmt: skip
    return jobs


@dataclass(frozen=True)
class Board:
    url: Callable[[str], str]
    parse: Callable[[Any], list[Job]]


BOARDS: dict[Source, Board] = {
    Source.GREENHOUSE: Board(
        lambda s: f"https://boards-api.greenhouse.io/v1/boards/{s}/jobs?content=true", _greenhouse
    ),
    Source.LEVER: Board(lambda s: f"https://api.lever.co/v0/postings/{s}?mode=json", _lever),
    Source.ASHBY: Board(lambda s: f"https://api.ashbyhq.com/posting-api/job-board/{s}", _ashby),
    Source.RECRUITEE: Board(lambda s: f"https://{s}.recruitee.com/api/offers/", _recruitee),
}


def to_vacancy(job: Job, company: Company) -> Vacancy:
    description = html_to_text(job.description_html)
    language = detect_language(description)
    title = " ".join(job.title.split())
    return Vacancy(
        source=company.ats,
        url=job.url,
        title=title,
        company=company.name,
        location=job.location or None,
        remote=job.work_format,
        seniority=detect_seniority(title),
        language_of_post=language,
        requires_polish=requires_polish(description, language),
        description=description,
        published_at=job.published_at,
    )


def parse_board(payload: Any, company: Company) -> list[Vacancy]:
    """The wanted jobs of one company's board; a broken job is logged and skipped."""
    vacancies = []
    for job in BOARDS[company.ats].parse(payload):
        if not (looks_like_qa(job.title) and open_to_candidate(job)):
            continue
        try:
            vacancies.append(to_vacancy(job, company))
        except ValueError as exc:
            logger.warning("%s: skipped %r: %r", company.name, job.url, exc)
    return vacancies


def fetch(client: httpx.Client, company: Company) -> Any:
    response = client.get(BOARDS[company.ats].url(company.slug))
    response.raise_for_status()
    return response.json()


def collect(
    client: httpx.Client,
    companies: list[Company] | None = None,
    *,
    delay: float = DELAY_BETWEEN_REQUESTS_SECONDS,
) -> list[Vacancy]:
    """One request per company; a failing board is logged and skipped."""
    companies = load_companies() if companies is None else companies
    vacancies: list[Vacancy] = []
    for i, company in enumerate(companies):
        if i:
            time.sleep(delay)
        try:
            vacancies += parse_board(fetch(client, company), company)
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            logger.warning("%s (%s): board failed: %r", company.name, company.ats, exc)
    return vacancies


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with make_client() as http:
        found = collect(http)
    for v in found:
        print(f"{v.source:<10} {v.title} @ {v.company}  | {v.location or '-'} · {v.remote or '?'}")
    print(f"\n{len(found)} vacancies")
