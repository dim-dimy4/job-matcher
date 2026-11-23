"""Company job boards (Greenhouse, Lever, Ashby, Recruitee): saved API responses and
hand-made jobs, `collect()` with HTTP mocked by respx. No real requests."""

import json
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import ValidationError

from job_matcher.collectors import ats
from job_matcher.collectors.ats import Company, Job
from job_matcher.collectors.http import USER_AGENT, make_client
from job_matcher.models import RemoteType, Seniority, Source

GREENHOUSE = Company(name="Ocado Technology", ats=Source.GREENHOUSE, slug="ocadogroup")
LEVER = Company(name="SwingDev", ats=Source.LEVER, slug="swingdev")
ASHBY = Company(name="Zeely AI", ats=Source.ASHBY, slug="zeely")
RECRUITEE = Company(name="The Codest", ats=Source.RECRUITEE, slug="thecodest")


@pytest.fixture
def board(read_fixture):
    return lambda name: json.loads(read_fixture(f"ats/{name}.json"))


def _job(location="", remote=None, title="QA Engineer"):
    return Job(title=title, url="https://example.com/job/1", location=location, remote=remote,
               description_html="<p>Manual testing.</p>", published_at=None)  # fmt: skip


# ---------- companies.yaml ----------


def test_the_real_company_list_loads():
    companies = ats.load_companies()

    assert companies, "config/companies.yaml lists no company"
    assert {c.ats for c in companies} <= set(ats.BOARDS)
    assert len({(c.ats, c.slug) for c in companies}) == len(companies), "no duplicates"


def test_a_typo_in_the_company_list_fails_loudly(tmp_path):
    path = tmp_path / "companies.yaml"
    path.write_text("companies:\n  - {name: Acme, ats: workday, slug: acme}\n", encoding="utf-8")

    with pytest.raises(ValidationError):
        ats.load_companies(path)


# ---------- who may apply ----------


@pytest.mark.parametrize(
    ("location", "remote", "expected"),
    [
        ("Warszawa, Poland", RemoteType.NO, True),
        ("Wrocław", None, True),
        ("Remote in Poland", RemoteType.YES, True),
        ("Europe, Kyiv, Ukraine, Warsaw, Remote, Lviv", RemoteType.YES, True),
        ("", RemoteType.YES, True),
        ("Remote", None, True),  # no stated format: "Remote" in the location counts
        ("Europe", RemoteType.YES, True),
        ("Remote - EMEA", RemoteType.YES, True),
        ("Remote - US", RemoteType.YES, False),
        ("Germany", RemoteType.YES, False),
        ("Europe", RemoteType.HYBRID, False),  # hybrid means an office somewhere
        ("London, United Kingdom", RemoteType.NO, False),
    ],
)
def test_open_to_candidate(location, remote, expected):
    assert ats.open_to_candidate(_job(location, remote)) is expected


def test_work_format_falls_back_to_the_location():
    assert _job("Remote (EU)", None).work_format is RemoteType.YES
    assert _job("Remote (EU)", RemoteType.HYBRID).work_format is RemoteType.HYBRID
    assert _job("Berlin", None).work_format is None


# ---------- each ATS's answer ----------


def test_greenhouse_board(board):
    jobs = ats.BOARDS[Source.GREENHOUSE].parse(board("greenhouse"))

    assert len(jobs) == 4
    job = jobs[0]
    assert job.url.startswith("https://careers.ocadogroup.com/jobs/details?gh_jid=")
    assert job.location == "Hatfield, United Kingdom"
    assert job.remote is None, "Greenhouse has no format field"
    assert job.published_at == datetime.fromisoformat("2026-09-17T04:51:20-04:00"), "first publish"
    assert "<" in job.description_html
    assert "&lt;" not in job.description_html, "Greenhouse escapes the HTML once more"


def test_lever_board(board):
    jobs = ats.BOARDS[Source.LEVER].parse(board("lever"))

    assert [(j.location, j.remote) for j in jobs] == [
        ("Warsaw, Poland", RemoteType.HYBRID),
        ("Remote in Poland, Poland", RemoteType.YES),
        ("Remote in Poland, Poland", RemoteType.YES),
    ]
    assert jobs[0].url.startswith("https://jobs.lever.co/swingdev/")
    assert jobs[0].published_at == datetime.fromtimestamp(1783602799.8, tz=UTC)
    assert "be doing</h3>" in jobs[0].description_html, "the lists are part of the text"


def test_ashby_board(board):
    jobs = ats.BOARDS[Source.ASHBY].parse(board("ashby"))

    qa = [j for j in jobs if "QA" in j.title]
    assert [(j.location, j.work_format) for j in qa] == [("Remote", RemoteType.YES)] * 2
    assert qa[0].url.startswith("https://jobs.ashbyhq.com/zeely/")
    assert qa[0].published_at == datetime(2026, 10, 6, 13, 56, 41, 22000, tzinfo=UTC)


def test_ashby_unlisted_jobs_are_skipped():
    payload = {
        "jobs": [{"title": "QA", "jobUrl": "https://jobs.ashbyhq.com/a/1", "isListed": False}]
    }

    assert ats.BOARDS[Source.ASHBY].parse(payload) == []


def test_recruitee_board(board):
    jobs = ats.BOARDS[Source.RECRUITEE].parse(board("recruitee"))

    job = jobs[0]
    assert job.title == "Senior QA Automation Engineer"
    assert job.url == "https://thecodest.recruitee.com/o/senior-qa-automation-engineer-4"
    assert (job.location, job.remote) == ("Warszawa, Poland", RemoteType.YES)
    assert job.published_at == datetime(2026, 10, 22, 7, 49, 9, tzinfo=UTC)


@pytest.mark.parametrize(
    ("flags", "expected"),
    [({"hybrid": True}, RemoteType.HYBRID), ({"on_site": True}, RemoteType.NO), ({}, None)],
    ids=["hybrid", "on-site", "not-stated"],
)
def test_recruitee_work_format(flags, expected):
    offer = {"title": "QA", "careers_url": "https://a.recruitee.com/o/qa", "locations": [],
             "remote": False, **flags}  # fmt: skip

    (job,) = ats.BOARDS[Source.RECRUITEE].parse({"offers": [offer]})

    assert (job.remote, job.published_at) == (expected, None)


def test_lever_without_a_creation_date():
    posting = {"text": "QA", "hostedUrl": "https://jobs.lever.co/a/1", "categories": {}}

    (job,) = ats.BOARDS[Source.LEVER].parse([posting])

    assert (job.location, job.published_at) == ("", None)


# ---------- from a board to vacancies ----------


@pytest.mark.parametrize(
    ("company", "fixture", "titles"),
    [
        (RECRUITEE, "recruitee", ["Senior QA Automation Engineer"]),
        (ASHBY, "ashby", ["Senior Manual QA", "Middle Manual QA Engineer (Growth)"]),
        (LEVER, "lever", []),  # no QA role on the board
        (GREENHOUSE, "greenhouse", []),
    ],
    ids=["recruitee", "ashby", "lever", "greenhouse"],
)
def test_only_qa_jobs_open_to_the_candidate_become_vacancies(board, company, fixture, titles):
    vacancies = ats.parse_board(board(fixture), company)

    assert [v.title for v in vacancies] == titles
    assert all(v.source is company.ats and v.company == company.name for v in vacancies)


def test_vacancy_fields(board):
    (v,) = ats.parse_board(board("recruitee"), RECRUITEE)

    assert v.seniority is Seniority.SENIOR
    assert v.remote is RemoteType.YES
    assert v.location == "Warszawa, Poland"
    assert v.description
    assert "<" not in v.description


def test_a_job_that_is_not_a_valid_vacancy_is_skipped(caplog):
    payload = {"offers": [{"title": "QA Engineer", "careers_url": "not a url",
                           "locations": [], "remote": True}]}  # fmt: skip

    assert ats.parse_board(payload, RECRUITEE) == []
    assert "The Codest: skipped" in caplog.text


# ---------- collect ----------


@pytest.fixture
def client():
    with make_client() as c:
        yield c


@pytest.fixture
def no_sleep(monkeypatch):
    calls: list[float] = []
    monkeypatch.setattr(ats.time, "sleep", calls.append)
    return calls


def test_collect_asks_every_board_once_and_politely(client, respx_mock, read_fixture, no_sleep):
    recruitee = respx_mock.get("https://thecodest.recruitee.com/api/offers/").mock(
        return_value=httpx.Response(200, content=read_fixture("ats/recruitee.json"))
    )
    ashby = respx_mock.get("https://api.ashbyhq.com/posting-api/job-board/zeely").mock(
        return_value=httpx.Response(200, content=read_fixture("ats/ashby.json"))
    )

    vacancies = ats.collect(client, [RECRUITEE, ASHBY], delay=1.5)

    assert len(vacancies) == 3
    assert (recruitee.call_count, ashby.call_count) == (1, 1)
    assert recruitee.calls.last.request.headers["User-Agent"] == USER_AGENT
    assert no_sleep == [1.5], "a pause between boards, not before the first"


@pytest.mark.parametrize(
    "failure",
    [httpx.Response(404), httpx.Response(200, content=b"<html>"), httpx.ConnectTimeout(""),
     httpx.Response(200, json={"unexpected": []})],
    ids=["no-such-board", "not-json", "timeout", "wrong-shape"],
)  # fmt: skip
def test_a_failing_board_skips_only_that_company(client, respx_mock, read_fixture, no_sleep,
                                                 caplog, failure):  # fmt: skip
    respx_mock.get("https://boards-api.greenhouse.io/v1/boards/ocadogroup/jobs?content=true").mock(
        side_effect=[failure]
    )
    respx_mock.get("https://thecodest.recruitee.com/api/offers/").mock(
        return_value=httpx.Response(200, content=read_fixture("ats/recruitee.json"))
    )

    vacancies = ats.collect(client, [GREENHOUSE, RECRUITEE])

    assert [v.company for v in vacancies] == ["The Codest"]
    assert "Ocado Technology (greenhouse): board failed" in caplog.text


def test_collect_reads_the_company_list_by_default(client, monkeypatch, no_sleep):
    monkeypatch.setattr(ats, "load_companies", lambda: [])

    assert ats.collect(client) == []
