"""Himalayas `collect()` with HTTP mocked by respx: no real requests leave the test."""

import httpx
import pytest

from job_matcher.collectors import himalayas
from job_matcher.collectors.http import USER_AGENT, make_client

REQUESTS = sum(himalayas.SEARCHES.values())  # 5: two pages of "QA", one of each other query


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
    """Serve the saved pages by (query, page); anything else is an empty page. A test can
    replace a response (or put an exception in its place)."""
    responses: dict[tuple[str, int], httpx.Response | Exception] = {
        ("QA", 1): httpx.Response(200, content=read_fixture("himalayas/qa_pl_page1.json")),
        ("QA", 2): httpx.Response(200, content=read_fixture("himalayas/qa_pl_page2.json")),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        key = (request.url.params["q"], int(request.url.params["page"]))
        result = responses.get(key, httpx.Response(200, json={"jobs": []}))
        if isinstance(result, Exception):
            raise result
        return result

    route = respx_mock.get(himalayas.SEARCH_URL).mock(side_effect=handler)
    return responses, route


def test_collect_merges_pages(client, pages, no_sleep):
    vacancies = himalayas.collect(client)

    assert len(vacancies) == 12
    assert len({v.url for v in vacancies}) == 12


def test_job_found_by_two_queries_is_kept_once(client, pages, no_sleep, read_fixture):
    responses, _ = pages
    responses[("Tester", 1)] = httpx.Response(
        200, content=read_fixture("himalayas/qa_pl_page1.json")
    )

    assert len(himalayas.collect(client)) == 12


def test_every_query_is_asked_with_the_search_params_and_user_agent(client, pages, no_sleep):
    _, route = pages

    himalayas.collect(client)

    asked = [(c.request.url.params["q"], c.request.url.params["page"]) for c in route.calls]
    assert asked == [("QA", "1"), ("QA", "2"), ("Quality Assurance", "1"),
                     ("Test Engineer", "1"), ("Tester", "1")]  # fmt: skip
    for call in route.calls:
        assert call.request.url.params["country"] == "PL"
        assert call.request.url.params["sort"] == "recent"
        assert call.request.headers["User-Agent"] == USER_AGENT


def test_waits_before_every_request_but_the_first(client, pages, no_sleep):
    himalayas.collect(client)

    assert no_sleep == [himalayas.DELAY_BETWEEN_PAGES_SECONDS] * (REQUESTS - 1)


def test_an_empty_page_ends_only_its_query(client, pages, no_sleep):
    responses, route = pages
    responses[("QA", 1)] = httpx.Response(200, json={"jobs": []})

    assert himalayas.collect(client) == []
    assert route.call_count == REQUESTS - 1, "page 2 of QA is skipped, the other queries run"


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
    responses[("QA", 2)] = failure

    vacancies = himalayas.collect(client)

    assert len(vacancies) == 6  # everything from page 1
    assert "'QA' page 2 failed" in caplog.text


def test_a_failing_query_does_not_stop_the_others(client, pages, no_sleep, read_fixture):
    responses, route = pages
    responses[("QA", 1)] = httpx.Response(503)
    responses[("Tester", 1)] = httpx.Response(
        200, content=read_fixture("himalayas/qa_pl_page2.json")
    )

    assert len(himalayas.collect(client)) == 6
    assert route.call_count == REQUESTS - 1, "no point asking for page 2 when page 1 is down"
