"""Contract: the live Jobicy API still returns jobs in the shape our parser expects.

Hits the real API, so it runs only on demand / on a schedule:
    pytest -m contract
"""

import pytest

from job_matcher.collectors import jobicy
from job_matcher.collectors.http import make_client

pytestmark = pytest.mark.contract

REQUIRED_FIELDS = {"jobTitle": str, "companyName": str, "url": str, "jobGeo": str,
                   "jobDescription": str, "pubDate": str}  # fmt: skip


def test_qa_industry_keeps_the_expected_shape():
    with make_client() as client:
        payload = jobicy.fetch(client)

    jobs = payload["jobs"]
    assert jobs, "no QA & Testing jobs in Europe this week: filters or format changed?"
    for job in jobs:
        wrong = [k for k, kind in REQUIRED_FIELDS.items() if not isinstance(job.get(k), kind)]
        assert not wrong, f"{job.get('url')}: missing or wrong type {wrong}"
    assert len(jobicy.parse_response(payload)) == len(jobs)
