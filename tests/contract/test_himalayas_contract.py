"""Contract: the live Himalayas API still returns jobs in the shape our parser expects.

Hits the real API, so it runs only on demand / on a schedule:
    pytest -m contract
"""

import pytest

from job_matcher.collectors import himalayas
from job_matcher.collectors.http import make_client

pytestmark = pytest.mark.contract

REQUIRED_FIELDS = {
    "title": str,
    "companyName": str,
    "guid": str,
    "applicationLink": str,
    "pubDate": int,
    "description": str,
    "seniority": list,
    "locationRestrictions": list,
}


def test_search_keeps_the_expected_shape():
    with make_client() as client:
        payload = himalayas.fetch_page(client, page=1)

    jobs = payload["jobs"]
    assert jobs, "search returned no jobs"
    for job in jobs:
        wrong = [k for k, kind in REQUIRED_FIELDS.items() if not isinstance(job.get(k), kind)]
        assert not wrong, f"{job.get('guid')}: missing or wrong type {wrong}"

    # Every job must still be parseable, otherwise parse_page skips it.
    assert len(himalayas.parse_page(payload)) == len(jobs)
