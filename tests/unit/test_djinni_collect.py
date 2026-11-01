"""Djinni `collect()` with HTTP mocked by respx: no real requests leave the test."""

import httpx
import pytest

from job_matcher.collectors import djinni
from job_matcher.collectors.http import USER_AGENT, make_client

JOB_IDS = ["855977", "848460", "855879", "854913", "853121", "852016"]  # feed order


@pytest.fixture
def client():
    with make_client() as c:
        yield c


@pytest.fixture
def no_sleep(monkeypatch):
    """Record the polite delay instead of actually waiting."""
    calls: list[float] = []
    monkeypatch.setattr(djinni.time, "sleep", calls.append)
    return calls


@pytest.fixture
def site(respx_mock, read_fixture):
    """Both feeds serve the saved feed; job pages are served by id. Tests replace entries."""
    feed = httpx.Response(200, content=read_fixture("djinni/rss_qa_automation.xml"))
    responses: dict[str, httpx.Response | Exception] = {"QA Automation": feed, "QA": feed}
    for ident in JOB_IDS:
        responses[ident] = httpx.Response(200, content=read_fixture(f"djinni/job_{ident}.html"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/jobs/rss/":
            key = request.url.params["primary_keyword"]
        else:
            key = request.url.path.split("/")[2].split("-")[0]
        result = responses[key]
        if isinstance(result, Exception):
            raise result
        return result

    route = respx_mock.get(host="djinni.co").mock(side_effect=handler)
    return responses, route


def test_collects_every_vacancy_once(client, site, no_sleep):
    found = djinni.collect(client)

    assert len(found) == 6, "the same item in both feeds is read once"
    assert {v.company for v in found} >= {"Adaptiq", "Glorium Technologies", "Kauri.One"}


def test_requests_are_filtered_paced_and_identified(client, site, no_sleep):
    _, route = site

    djinni.collect(client, delay=1.5)

    feed_request = route.calls[0].request
    assert feed_request.url.params["employment"] == "remote"
    assert feed_request.url.params["exp_level"] == "5y"
    assert feed_request.headers["User-Agent"] == USER_AGENT
    assert route.call_count == 2 + 6
    assert no_sleep == [1.5] * 7, "a pause before every request but the first"


def test_job_pages_are_capped_newest_first(client, site, no_sleep, monkeypatch):
    monkeypatch.setattr(djinni, "MAX_JOB_PAGES", 2)

    found = djinni.collect(client)

    assert [v.company for v in found] == ["Adaptiq", "N-iX"]


@pytest.mark.parametrize(
    "failure",
    [httpx.Response(500), httpx.Response(200, content=b"<rss><broken"), httpx.ReadTimeout("")],
    ids=["http-500", "broken-xml", "timeout"],
)
def test_a_failing_feed_keeps_the_other(client, site, no_sleep, failure, caplog):
    responses, _ = site
    responses["QA Automation"] = failure

    assert len(djinni.collect(client)) == 6
    assert "Djinni: feed 'QA Automation' failed" in caplog.text


@pytest.mark.parametrize(
    "failure",
    [
        httpx.Response(404),
        httpx.Response(200, content=b"<html>no structured data</html>"),
        httpx.Response(200, content=b'<script type="application/ld+json">'
                                    b'{"@type": "JobPosting", "title": "QA"}</script>'),
        httpx.ConnectError(""),
    ],
    ids=["http-404", "no-json-ld", "no-company", "connection-error"],
)  # fmt: skip
def test_a_failing_job_page_skips_only_that_vacancy(client, site, no_sleep, failure, caplog):
    responses, _ = site
    responses["853121"] = failure

    found = djinni.collect(client)

    assert len(found) == 5
    assert "Glorium Technologies" not in {v.company for v in found}
    assert "Djinni: skipped https://djinni.co/jobs/853121-qa-lead/" in caplog.text


def test_both_feeds_failing_gives_nothing(client, site, no_sleep):
    responses, route = site
    responses["QA Automation"] = responses["QA"] = httpx.Response(503)

    assert djinni.collect(client) == []
    assert route.call_count == 2
