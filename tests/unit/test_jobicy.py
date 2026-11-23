"""Jobicy: parsing a saved API response and `collect()` with HTTP mocked by respx."""

import json
from datetime import UTC, datetime

import httpx
import pytest

from job_matcher.collectors import jobicy
from job_matcher.collectors.http import USER_AGENT, make_client
from job_matcher.models import RemoteType, Seniority, Source


@pytest.fixture
def payload(read_fixture):
    return json.loads(read_fixture("jobicy/qa_testing_europe.json"))


@pytest.fixture
def vacancies(payload):
    return jobicy.parse_response(payload)


def _find(vacancies, title, location):
    return next(v for v in vacancies if v.title.startswith(title) and v.location == location)


def _job(**overrides):
    job = {
        "jobTitle": "QA Engineer",
        "companyName": "Acme",
        "url": "https://jobicy.com/jobs/1-qa-engineer",
        "jobGeo": "Europe",
        "jobLevel": "Any",
        "jobDescription": "<p>Manual and automated testing.</p>",
        "pubDate": "2026-11-20T14:19:26+00:00",
    }
    return {**job, **overrides}


def test_every_job_in_the_response_is_parsed(payload, vacancies):
    assert len(vacancies) == len(payload["jobs"]) == 7
    assert {v.source for v in vacancies} == {Source.JOBICY}
    assert {v.remote for v in vacancies} == {RemoteType.YES}


def test_fixture_job_with_a_yearly_salary(vacancies):
    v = _find(vacancies, "Senior QA Engineer", "Spain")

    assert str(v.url).startswith("https://jobicy.com/jobs/"), "the Jobicy page, as the terms ask"
    assert v.seniority is Seniority.SENIOR
    assert (v.salary_min, v.salary_max, v.currency) == (4583, 5833, "EUR")
    assert v.published_at == datetime(2026, 11, 18, 12, 42, 34, tzinfo=UTC)
    assert v.description


def test_location_list_is_tidied(vacancies):
    assert _find(vacancies, "Engineering Manager", "EMEA, LATAM, Canada, USA")


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"salaryMin": 60000, "salaryMax": 72000, "salaryCurrency": "EUR",
          "salaryPeriod": "yearly"}, (5000, 6000, "EUR")),
        ({"salaryMin": 5000, "salaryCurrency": "USD", "salaryPeriod": "monthly"},
         (5000, None, "USD")),
        ({"salaryMin": 27, "salaryMax": 27, "salaryCurrency": "USD", "salaryPeriod": "hourly"},
         (None, None, None)),
        ({"salaryMin": 16, "salaryMax": 16, "salaryPeriod": "yearly"}, (None, None, None)),
        ({}, (None, None, None)),
    ],
    ids=["yearly", "monthly-min-only", "hourly", "no-currency", "none"],
)  # fmt: skip
def test_salary(fields, expected):
    v = jobicy.parse_job(_job(**fields))

    assert (v.salary_min, v.salary_max, v.currency) == expected


@pytest.mark.parametrize(
    ("title", "level", "expected"),
    [
        ("Middle QA Engineer", "Senior", Seniority.MIDDLE),  # the title wins
        ("QA Engineer", "Midweight", Seniority.MIDDLE),
        ("QA Engineer", "Entry-Level, Junior", Seniority.JUNIOR),
        ("QA Engineer", "Junior, Senior", None),  # ambiguous
        ("QA Engineer", "Any", None),
        ("QA Engineer", None, None),
    ],
)
def test_seniority(title, level, expected):
    assert jobicy.parse_job(_job(jobTitle=title, jobLevel=level)).seniority is expected


def test_broken_job_is_skipped(caplog):
    payload = {"jobs": [_job(), {"jobTitle": "No company or URL"}]}

    assert len(jobicy.parse_response(payload)) == 1
    assert "Jobicy: skipped job" in caplog.text


def test_response_without_jobs_is_an_error():
    with pytest.raises(ValueError, match="no 'jobs' list"):
        jobicy.parse_response({"error": "rate limited"})


@pytest.fixture
def client():
    with make_client() as c:
        yield c


def test_collect_asks_once_with_the_qa_industry(client, respx_mock, read_fixture):
    route = respx_mock.get(jobicy.API_URL).mock(
        return_value=httpx.Response(200, content=read_fixture("jobicy/qa_testing_europe.json"))
    )

    assert len(jobicy.collect(client)) == 7
    assert route.call_count == 1
    request = route.calls.last.request
    assert dict(request.url.params) == {"geo": "europe", "industry": "qa-testing", "count": "100"}
    assert request.headers["User-Agent"] == USER_AGENT


@pytest.mark.parametrize(
    "failure",
    [httpx.Response(429), httpx.Response(200, content=b"<html>"), httpx.ConnectTimeout("")],
    ids=["rate-limited", "not-json", "timeout"],
)
def test_a_failing_request_gives_nothing(client, respx_mock, caplog, failure):
    respx_mock.get(jobicy.API_URL).mock(side_effect=[failure])

    assert jobicy.collect(client) == []
    assert "Jobicy: request failed" in caplog.text
