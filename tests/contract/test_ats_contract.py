"""Contract: every board in config/companies.yaml still answers in the shape we parse.

Hits the real APIs (one request per company, 1 s apart), so it runs only on demand / on a
schedule:
    pytest -m contract
"""

import time

import pytest

from job_matcher.collectors import ats
from job_matcher.collectors.http import make_client

pytestmark = pytest.mark.contract


@pytest.fixture(scope="module")
def boards():
    answers = []
    with make_client() as client:
        for i, company in enumerate(ats.load_companies()):
            if i:
                time.sleep(ats.DELAY_BETWEEN_REQUESTS_SECONDS)
            answers.append((company, ats.fetch(client, company)))
    return answers


def test_every_board_still_answers(boards):
    for company, payload in boards:
        jobs = ats.BOARDS[company.ats].parse(payload)
        assert isinstance(jobs, list), company.name
        for job in jobs:
            assert job.title, f"{company.name}: a job without a title"
            assert job.url.startswith("https://"), f"{company.name}: {job.url!r}"


def test_the_boards_are_not_all_empty(boards):
    total = sum(len(ats.BOARDS[c.ats].parse(p)) for c, p in boards)

    assert total > 0, "no jobs on any board: a slug or an API changed?"
