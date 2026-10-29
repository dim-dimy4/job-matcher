"""Himalayas `collect()` with HTTP mocked by respx: no real requests leave the test."""

import httpx
import pytest

from job_matcher.collectors import himalayas
from job_matcher.collectors.http import USER_AGENT, make_client


@pytest.fixture
def client():
    with make_client() as c:
        yield c


@pytest.fixture
def no_sleep(monkeypatch):
    """Record the polite delay instead of actually waiting."""
    calls: list[float] = []
    monkeypatch.setattr(himalayas.time, "sleep", calls.append)
    return calls


@pytest.fixture
def pages(respx_mock, read_fixture):
    """Serve the saved pages by `?page=N`. A test can replace a page's response (or exception)."""
    responses: dict[int, httpx.Response | Exception] = {
        1: httpx.Response(200, content=read_fixture("himalayas/qa_pl_page1.json")),
        2: httpx.Response(200, content=read_fixture("himalayas/qa_pl_page2.json")),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        result = responses.get(
            int(request.url.params["page"]), httpx.Response(200, json={"jobs": []})
        )
        if isinstance(result, Exception):
            raise result
        return result

    route = respx_mock.get(himalayas.SEARCH_URL).mock(side_effect=handler)
    return responses, route


def test_collect_merges_pages(client, pages, no_sleep):
    vacancies = himalayas.collect(client)

    assert len(vacancies) == 40
    assert len({v.id for v in vacancies}) == 40


def test_same_job_on_two_pages_is_kept_once(client, pages, no_sleep, read_fixture):
    responses, _ = pages
    responses[2] = httpx.Response(200, content=read_fixture("himalayas/qa_pl_page1.json"))

    assert len(himalayas.collect(client)) == 20


def test_requests_carry_search_params_and_user_agent(client, pages, no_sleep):
    _, route = pages

    himalayas.collect(client)

    assert route.call_count == himalayas.MAX_PAGES
    for page, call in enumerate(route.calls, start=1):
        assert dict(call.request.url.params) == {**himalayas.SEARCH_PARAMS, "page": str(page)}
        assert call.request.headers["User-Agent"] == USER_AGENT


def test_waits_between_pages(client, pages, no_sleep):
    himalayas.collect(client)

    assert no_sleep == [himalayas.DELAY_BETWEEN_PAGES_SECONDS] * (himalayas.MAX_PAGES - 1)


def test_stops_at_empty_page(client, pages, no_sleep):
    responses, route = pages
    responses[1] = httpx.Response(200, json={"jobs": []})

    assert himalayas.collect(client) == []
    assert route.call_count == 1
    assert no_sleep == []


@pytest.mark.parametrize(
    "failure",
    [
        httpx.Response(500),
        httpx.Response(429),
        httpx.Response(200, content=b"<html>not json</html>"),
        httpx.Response(200, json={"error": "rate limited"}),
        httpx.ConnectTimeout("timed out"),
    ],
    ids=["http-500", "http-429", "not-json", "no-jobs-key", "timeout"],
)
def test_failing_page_keeps_what_was_collected(client, pages, no_sleep, caplog, failure):
    responses, _ = pages
    responses[2] = failure

    vacancies = himalayas.collect(client)

    assert len(vacancies) == 20  # everything from page 1
    assert "page 2 failed" in caplog.text


def test_failing_first_page_returns_empty_list(client, respx_mock, no_sleep):
    route = respx_mock.get(himalayas.SEARCH_URL).mock(return_value=httpx.Response(503))

    assert himalayas.collect(client) == []
    assert route.call_count == 1  # no point asking for page 2 when page 1 is down
