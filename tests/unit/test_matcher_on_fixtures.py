"""The real profile on real saved vacancies: a sanity check that the rules pick sensible jobs.

Assertions are deliberately loose (above / below the threshold), so tuning the profile
doesn't require rewriting them; exact rule behaviour is covered in test_matcher.py.
"""

import json
from pathlib import Path

import pytest

from job_matcher.collectors import dou, himalayas
from job_matcher.matcher import apply
from job_matcher.profile import load_profile

FIXTURES_DIR = Path(__file__).parents[1] / "fixtures"


@pytest.fixture(scope="module")
def scored():
    vacancies = dou.parse_feed((FIXTURES_DIR / "dou/qa_remote.xml").read_bytes())
    vacancies += dou.parse_feed((FIXTURES_DIR / "dou/qa_relocation.xml").read_bytes())
    for page in ("qa_pl_page1.json", "qa_pl_page2.json"):
        vacancies += himalayas.parse_page(
            json.loads((FIXTURES_DIR / "himalayas" / page).read_bytes())
        )
    profile = load_profile(env={})  # example thresholds, not the private ones
    return {(v.company, v.title): apply(v, profile) for v in vacancies}, profile


def _get(scored, company, title):
    vacancies, _ = scored
    return next(v for (c, t), v in vacancies.items() if c.startswith(company) and t == title)


@pytest.mark.parametrize(
    ("company", "title"),
    [
        ("Codest", "Senior QA Automation Engineer"),
        ("Glorium", "Middle/Senior AQA"),
        ("FinteqHub", "QA Tech Lead"),
    ],
)
def test_good_matches_reach_the_threshold(scored, company, title):
    _, profile = scored

    assert _get(scored, company, title).score >= profile.notify_threshold


@pytest.mark.parametrize(
    ("company", "title", "reason"),
    [
        ("micro1", "Family Medicine Hospitalist", "not a QA role"),
        ("G2i", "Join the G2i Talent Pool", "not a QA role"),
        ("Future Processing", "Senior Quality Engineer (Playwright)", "requires Polish"),
        ("Spotter Labs", "Quality Assurance Engineer -- Junior Product Manager | AI / Trucking",
         "junior"),
        ("Synergetica", "Head of QA (in Warsaw)", "location"),
    ],
)  # fmt: skip
def test_noise_is_rejected(scored, company, title, reason):
    v = _get(scored, company, title)

    assert v.score == 0
    assert any(reason in r for r in v.match_reasons), v.match_reasons
