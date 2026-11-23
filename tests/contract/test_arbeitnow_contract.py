"""Contract: the live Arbeitnow API still returns jobs in the shape our parser expects.

Hits the real API, so it runs only on demand / on a schedule:
    pytest -m contract
"""

import pytest

from job_matcher.collectors import arbeitnow
from job_matcher.collectors.http import make_client

pytestmark = pytest.mark.contract

REQUIRED_FIELDS = {"title": str, "company_name": str, "url": str, "location": str,
                   "description": str, "remote": bool, "created_at": int}  # fmt: skip


def test_first_page_keeps_the_expected_shape():
    with make_client() as client:
        payload = arbeitnow.fetch(client)

    jobs = payload["data"]
    assert len(jobs) > 100, "the first page should hold the latest few hundred jobs"
    for job in jobs:
        wrong = [k for k, kind in REQUIRED_FIELDS.items() if not isinstance(job.get(k), kind)]
        assert not wrong, f"{job.get('url')}: missing or wrong type {wrong}"
    wanted = [job for job in jobs if arbeitnow.is_wanted(job)]
    assert len(arbeitnow.parse_page(payload)) == len(wanted)
