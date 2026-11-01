"""Himalayas parsing, offline: saved API responses and hand-made job objects, no network."""

import json
import logging
from typing import Any

import pytest

from job_matcher.collectors.himalayas import parse_job, parse_page
from job_matcher.models import ContractType, Language, RemoteType, Seniority, Source


def _job(**overrides: Any) -> dict[str, Any]:
    """A minimal valid job in the API's shape; each test overrides only what it is about."""
    job: dict[str, Any] = {
        "title": "QA Engineer",
        "companyName": "Acme",
        "employmentType": "Full Time",
        "minSalary": None,
        "maxSalary": None,
        "salaryPeriod": "annual",
        "currency": None,
        "seniority": [],
        "locationRestrictions": ["Poland"],
        "description": "<p>We are looking for a QA engineer to join the team.</p>",
        "pubDate": 1793265096,
        "guid": "https://himalayas.app/companies/acme/jobs/qa-engineer-1",
    }
    job.update(overrides)
    return job


@pytest.fixture
def load_page(read_fixture):
    return lambda name: json.loads(read_fixture(name))


def _find(vacancies, company):
    return next(v for v in vacancies if v.company.startswith(company))


@pytest.mark.parametrize("name", ["himalayas/qa_pl_page1.json", "himalayas/qa_pl_page2.json"])
def test_fixture_page_parses_every_job(load_page, caplog, name):
    vacancies = parse_page(load_page(name))

    assert len(vacancies) == 6
    assert not caplog.records, "no job should be skipped"
    for v in vacancies:
        assert v.source is Source.HIMALAYAS
        assert v.remote is RemoteType.YES
        assert "utm_" not in str(v.url)
        assert v.published_at.tzinfo is not None


def test_fixture_vacancy_fields(load_page):
    v = _find(parse_page(load_page("himalayas/qa_pl_page1.json")), "Codest")

    assert v.title == "Senior QA Automation Engineer"
    assert v.company == "Codest Ltd.", "registration number and VAT are cut off"
    assert v.seniority is Seniority.SENIOR
    assert v.location == "Poland"
    assert (v.salary_min, v.salary_max, v.currency) == (18000, 23000, "PLN")  # monthly as is
    assert v.language_of_post is Language.EN
    assert v.requires_polish is False


def test_fixture_annual_salary_becomes_monthly(load_page):
    v = _find(parse_page(load_page("himalayas/qa_pl_page2.json")), "Veeva")

    assert (v.salary_min, v.salary_max, v.currency) == (20000, 31667, "PLN")


def test_fixture_polish_post_requires_polish(load_page):
    v = _find(parse_page(load_page("himalayas/qa_pl_page2.json")), "Future Processing")

    assert v.language_of_post is Language.PL
    assert v.requires_polish is True


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        (
            {"salaryPeriod": "monthly", "minSalary": 18000, "maxSalary": 23000, "currency": "PLN"},
            (18000, 23000, "PLN"),
        ),
        (
            {"salaryPeriod": "annual", "minSalary": 60000, "maxSalary": 90000, "currency": "USD"},
            (5000, 7500, "USD"),
        ),
        (
            {"salaryPeriod": "annual", "minSalary": None, "maxSalary": 120000, "currency": "EUR"},
            (None, 10000, "EUR"),
        ),
        (
            {"salaryPeriod": "hourly", "minSalary": 6, "maxSalary": 40, "currency": "USD"},
            (None, None, None),
        ),
        (
            {"salaryPeriod": "annual", "minSalary": 60000, "maxSalary": 90000, "currency": None},
            (None, None, None),
        ),
        (
            {"salaryPeriod": "annual", "minSalary": None, "maxSalary": None, "currency": "USD"},
            (None, None, None),
        ),
    ],
    ids=["monthly", "annual-to-monthly", "only-max", "hourly-dropped", "no-currency", "no-amount"],
)
def test_salary(fields, expected):
    v = parse_job(_job(**fields))

    assert (v.salary_min, v.salary_max, v.currency) == expected


@pytest.mark.parametrize(
    ("title", "levels", "expected"),
    [
        ("Senior QA Engineer", ["Mid-level"], Seniority.SENIOR),
        ("QA Engineer", ["Mid-level"], Seniority.MIDDLE),
        ("QA Engineer", ["Manager"], Seniority.LEAD),
        ("QA Engineer", ["Manager", "Director"], Seniority.LEAD),
        ("QA Engineer", ["Mid-level", "Senior"], None),
        ("QA Engineer", ["Intern"], None),
        ("QA Engineer", [], None),
    ],
    ids=[
        "title-wins",
        "single-level",
        "manager-is-lead",
        "same-grade-twice",
        "ambiguous",
        "unknown-level",
        "no-levels",
    ],
)
def test_seniority(title, levels, expected):
    assert parse_job(_job(title=title, seniority=levels)).seniority is expected


@pytest.mark.parametrize(
    ("countries", "expected"),
    [
        ([], "Worldwide"),
        (["Poland"], "Poland"),
        (["Poland", "Serbia", "Spain"], "Poland, Serbia, Spain"),
        (["Austria", "Poland", "Serbia", "Spain", "Sweden"], "Poland, Austria, Serbia +2 more"),
        (["Albania", "Andorra", "Austria", "Poland"], "Poland, Albania, Andorra +1 more"),
    ],
    ids=["worldwide", "one", "three", "shortened", "poland-first"],
)
def test_location(countries, expected):
    assert parse_job(_job(locationRestrictions=countries)).location == expected


@pytest.mark.parametrize(
    ("employment", "expected"),
    [("Contractor", ContractType.B2B), ("Full Time", None), ("Part Time", None)],
)
def test_contract_type(employment, expected):
    assert parse_job(_job(employmentType=employment)).contract_type is expected


def test_published_at_is_utc():
    v = parse_job(_job(pubDate=1793265096))

    assert v.published_at.isoformat() == "2026-10-29T09:11:36+00:00"


@pytest.mark.parametrize(
    "broken",
    [
        _job(title=None),
        _job(guid="not-a-url"),
        _job(pubDate="yesterday"),
        {key: value for key, value in _job().items() if key != "companyName"},
    ],
    ids=["null-title", "bad-url", "bad-date", "missing-company"],
)
def test_broken_job_is_skipped_not_fatal(caplog, broken):
    caplog.set_level(logging.WARNING)

    vacancies = parse_page({"jobs": [_job(), broken]})

    assert [str(v.url) for v in vacancies] == [_job()["guid"]]
    assert "skipped job" in caplog.text


@pytest.mark.parametrize("payload", [{}, {"jobs": None}, {"error": "rate limited"}])
def test_response_without_jobs_list_is_rejected(payload):
    with pytest.raises(ValueError, match="no 'jobs' list"):
        parse_page(payload)
