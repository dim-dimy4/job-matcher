"""Contract: the live DOU feeds still look like our fixtures.

Unit tests prove our parser handles the saved responses; this proves DOU still sends
responses of that shape. Hits the real site, so it runs only on demand / on a schedule:
    pytest -m contract
"""

import pytest
from defusedxml.ElementTree import fromstring

from job_matcher.collectors import dou
from job_matcher.collectors.http import make_client

pytestmark = pytest.mark.contract

REQUIRED_TAGS = ("title", "link", "description", "pubDate")


@pytest.fixture(scope="module")
def client():
    with make_client() as c:
        yield c


@pytest.mark.parametrize("url", dou.FEED_URLS)
def test_feed_keeps_the_expected_shape(client, url):
    raw = dou.fetch_feed(client, url)

    items = fromstring(raw).findall("./channel/item")
    assert items, "feed has no <item> elements"
    for item in items:
        missing = [tag for tag in REQUIRED_TAGS if not item.findtext(tag)]
        assert not missing, f"{item.findtext('link')}: missing {missing}"

    # Every item must still match the title format, otherwise parse_feed skips it.
    assert len(dou.parse_feed(raw)) == len(items)
