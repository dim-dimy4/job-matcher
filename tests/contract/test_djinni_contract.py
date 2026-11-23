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


def test_experience_buckets_are_a_union(client):
    """exp_level values are exact buckets; repeated, they must add up, not intersect."""
    base = {"primary_keyword": "QA Automation", "employment": "remote"}
    one = djinni.parse_feed(djinni.fetch(client, djinni.FEED_URL, {**base, "exp_level": "5y"}))
    all_ = djinni.parse_feed(djinni.fetch(client, djinni.FEED_URL, {**base, **djinni.FEED_FILTERS}))

    assert {i.url for i in one} <= {i.url for i in all_}
    assert len(all_) > len(one), "2y+3y+5y should be wider than 5y alone"


def test_job_page_has_a_job_posting(client, items):
    job = djinni.find_job_posting(djinni.fetch(client, items[0].url))

    assert isinstance(job["hiringOrganization"]["name"], str)
    assert isinstance(job["title"], str)
    assert djinni.parse_job(job, items[0]).company
