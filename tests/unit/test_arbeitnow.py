"""Arbeitnow: filtering and parsing a saved API page, and `collect()` with HTTP mocked."""

import json
from datetime import UTC, datetime

import httpx
import pytest

from job_matcher.collectors import arbeitnow
from job_matcher.collectors.http import USER_AGENT, make_client
from job_matcher.models import RemoteType, Source


def _job(**overrides):
    job = {
        "slug": "qa-engineer-1",
        "company_name": "Acme GmbH",
        "title": "QA Engineer",
        "description": "<p>Manual and automated testing.</p>",
        "remote": True,
        "url": "https://www.arbeitnow.com/jobs/companies/acme/qa-engineer-1",
        "tags": [],
        "job_types": [],
        "location": "Berlin",
        "created_at": 1795350674,
    }
    return {**job, **overrides}


@pytest.mark.parametrize(
    ("fields", "wanted"),
    [
        ({}, True),
        ({"title": "Software Test Engineer"}, True),
        ({"title": "Quality Assurance Specialist", "remote": False, "location": "Wrocław"}, True),
        ({"title": "QA Engineer", "remote": False, "location": "Warszawa, Poland"}, True),
        ({"title": "QA Engineer", "remote": False, "location": "Berlin"}, False),
        ({"title": "Marketplace Manager"}, False),
        ({"title": "Latest Trends Analyst"}, False),  # "test" inside a word
    ],
    ids=["qa-remote", "tester-remote", "poland-city", "poland-country", "on-site-germany",
         "not-qa", "not-a-substring"],
)  # fmt: skip
def test_is_wanted(fields, wanted):
    assert arbeitnow.is_wanted(_job(**fields)) is wanted


def test_fixture_page_keeps_only_qa_jobs_open_to_the_candidate(read_fixture):
    payload = json.loads(read_fixture("arbeitnow/page1.json"))

    vacancies = arbeitnow.parse_page(payload)

    assert len(payload["data"]) == 6
    assert [v.title for v in vacancies] == ["Software Test Engineer"]
    v = vacancies[0]
    assert v.source is Source.ARBEITNOW
    assert v.remote is RemoteType.YES
    assert v.location is None, '"Remote job" is not a place'
    assert str(v.url).startswith("https://www.arbeitnow."), "the Arbeitnow page, as the terms ask"
    assert v.description


def test_parse_job_fields():
    v = arbeitnow.parse_job(_job(title="Senior  QA Engineer", remote=False, location="Wrocław"))

    assert (v.title, v.company, v.location, v.remote) == (
        "Senior QA Engineer", "Acme GmbH", "Wrocław", None,
    )  # fmt: skip
    assert v.seniority == "senior"
    assert v.published_at == datetime.fromtimestamp(1795350674, tz=UTC)


def test_broken_job_is_skipped(caplog):
    payload = {"data": [_job(), _job(company_name=None)]}

    assert len(arbeitnow.parse_page(payload)) == 1
    assert "Arbeitnow: skipped job" in caplog.text


def test_response_without_data_is_an_error():
    with pytest.raises(ValueError, match="no 'data' list"):
        arbeitnow.parse_page({"message": "Too Many Attempts."})


@pytest.fixture
def client():
    with make_client() as c:
        yield c


def test_collect_reads_only_the_first_page(client, respx_mock, read_fixture):
    route = respx_mock.get(arbeitnow.API_URL).mock(
        return_value=httpx.Response(200, content=read_fixture("arbeitnow/page1.json"))
    )

    assert len(arbeitnow.collect(client)) == 1
    assert route.call_count == 1
    assert route.calls.last.request.url.params["page"] == "1"
    assert route.calls.last.request.headers["User-Agent"] == USER_AGENT


@pytest.mark.parametrize(
    "failure",
    [httpx.Response(429), httpx.Response(200, content=b"<html>"), httpx.ConnectTimeout("")],
    ids=["rate-limited", "not-json", "timeout"],
)
def test_a_failing_request_gives_nothing(client, respx_mock, caplog, failure):
    respx_mock.get(arbeitnow.API_URL).mock(side_effect=[failure])

    assert arbeitnow.collect(client) == []
    assert "Arbeitnow: request failed" in caplog.text
