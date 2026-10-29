"""DOU `collect()` with HTTP mocked by respx: no real requests leave the test."""

import httpx
import pytest

from job_matcher.collectors import dou
from job_matcher.collectors.http import USER_AGENT, make_client

REMOTE_URL, RELOCATION_URL = dou.FEED_URLS


@pytest.fixture
def client():
    with make_client() as c:
        yield c


@pytest.fixture
def no_sleep(monkeypatch):
    """Record the polite delay instead of actually waiting."""
    calls: list[float] = []
    monkeypatch.setattr(dou.time, "sleep", calls.append)
    return calls


@pytest.fixture
def feeds(respx_mock, read_fixture):
    """Serve the saved fixtures. A test can replace a feed's response (or exception) by URL."""
    responses: dict[str, httpx.Response | Exception] = {
        REMOTE_URL: httpx.Response(200, content=read_fixture("dou/qa_remote.xml")),
        RELOCATION_URL: httpx.Response(200, content=read_fixture("dou/qa_relocation.xml")),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        result = responses[str(request.url)]
        if isinstance(result, Exception):
            raise result
        return result

    route = respx_mock.get(host="jobs.dou.ua").mock(side_effect=handler)
    return responses, route


def test_collect_merges_feeds_and_deduplicates(client, feeds, no_sleep):
    vacancies = dou.collect(client)

    # 6 + 6 items, 3 vacancies appear in both feeds.
    assert len(vacancies) == 9
    assert len({v.id for v in vacancies}) == 9


def test_requests_carry_contact_user_agent(client, feeds, no_sleep):
    _, route = feeds

    dou.collect(client)

    assert route.call_count == len(dou.FEED_URLS)
    for call in route.calls:
        assert call.request.headers["User-Agent"] == USER_AGENT


def test_waits_between_feeds(client, feeds, no_sleep):
    dou.collect(client)

    assert no_sleep == [dou.DELAY_BETWEEN_FEEDS_SECONDS]


@pytest.mark.parametrize(
    "failure",
    [
        httpx.Response(500),
        httpx.Response(404),
        httpx.Response(200, content=b"<rss><channel>"),  # truncated XML
        httpx.ConnectTimeout("timed out"),
    ],
    ids=["http-500", "http-404", "broken-xml", "timeout"],
)
def test_one_failing_feed_does_not_lose_the_other(client, feeds, no_sleep, caplog, failure):
    responses, _ = feeds
    responses[REMOTE_URL] = failure

    vacancies = dou.collect(client)

    assert len(vacancies) == 6  # everything from the relocation feed
    assert f"feed {REMOTE_URL} failed" in caplog.text


def test_all_feeds_failing_returns_empty_list(client, respx_mock, no_sleep):
    respx_mock.get(host="jobs.dou.ua").mock(return_value=httpx.Response(503))

    assert dou.collect(client) == []
