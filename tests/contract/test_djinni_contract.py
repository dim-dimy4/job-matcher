"""Contract: the live Djinni feed and job pages still have what the collector reads.

Hits the real site, so it runs only on demand / on a schedule:
    pytest -m contract
"""

import pytest

from job_matcher.collectors import djinni
from job_matcher.collectors.http import make_client

pytestmark = pytest.mark.contract


@pytest.fixture(scope="module")
def client():
    with make_client() as c:
        yield c


@pytest.fixture(scope="module")
def items(client):
    feed = djinni.fetch(
        client, djinni.FEED_URL, {"primary_keyword": "QA Automation", **djinni.FEED_FILTERS}
    )
    return djinni.parse_feed(feed)


def test_feed_has_items(items):
    assert items, "the QA Automation feed is empty: filters or format changed?"


def test_job_page_has_a_job_posting(client, items):
    job = djinni.find_job_posting(djinni.fetch(client, items[0].url))

    assert isinstance(job["hiringOrganization"]["name"], str)
    assert isinstance(job["title"], str)
    assert djinni.parse_job(job, items[0]).company
