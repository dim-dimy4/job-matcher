"""Matcher rules, table-driven: one vacancy per row, only the fields that matter for the rule.

A test profile is used instead of config/profile.yaml, so editing the real profile never
breaks these tests.
"""

import pytest

from job_matcher.matcher import apply, score
from job_matcher.profile import Profile


@pytest.fixture
def profile() -> Profile:
    return Profile(
        role_keywords=["QA", "SDET", "test automation"],
        cities=["Wrocław"],
        plus_keywords=["Playwright", "Python", "Pytest", "API", "Postman", "LLM", "AI"],
        minus_keywords=["casino", "betting"],
        min_monthly_salary={"PLN": 20000, "USD": 5000},
    )


@pytest.fixture
def vacancy(make_vacancy):
    """A remote Senior QA vacancy with no keywords: role + seniority + remote = 60."""

    def _make(**overrides):
        return make_vacancy(**{"remote": "yes", "seniority": "senior", **overrides})

    return _make


def test_baseline_score_and_reasons(profile, vacancy):
    match = score(vacancy(), profile)

    assert match.score == 60
    assert match.reasons == ["+30 role: QA", "+20 seniority: senior", "+10 remote"]


def test_perfect_vacancy_scores_100(profile, vacancy):
    v = vacancy(
        description="Playwright, Python, Pytest, REST API, Postman, LLM and AI testing",
        salary_min=20000,
        salary_max=26000,
        currency="PLN",
    )

    assert score(v, profile).score == 100


@pytest.mark.parametrize(
    ("description", "points", "found"),
    [
        ("Python and Playwright", 10, ["Playwright", "Python"]),
        ("python, PYTEST", 10, ["Python", "Pytest"]),
        ("testing REST APIs", 5, ["API"]),
        ("rapid releases, maintain the suite", 0, []),  # 'api' / 'ai' inside other words
        ("Playwright Python Pytest API Postman LLM AI", 30, None),  # 7 x 5 capped at 30
    ],
    ids=["two", "case-insensitive", "plural", "not-a-substring", "capped"],
)
def test_plus_keywords(profile, vacancy, description, points, found):
    match = score(vacancy(description=description), profile)

    assert match.score == 60 + points
    if found is not None:
        assert match.keywords == found


@pytest.mark.parametrize(
    ("seniority", "expected"),
    [("senior", 60), ("lead", 60), ("middle", 40), (None, 40)],
)
def test_seniority_points(profile, vacancy, seniority, expected):
    assert score(vacancy(seniority=seniority), profile).score == expected


@pytest.mark.parametrize(
    ("fields", "points", "reason"),
    [
        ({"salary_min": 18000, "salary_max": 23000, "currency": "PLN"}, 10,
         "+10 salary up to 23000 PLN >= 20000"),
        ({"salary_min": 6000, "currency": "USD"}, 10, "+10 salary up to 6000 USD >= 5000"),
        ({"salary_max": 15000, "currency": "PLN"}, 0, "salary up to 15000 PLN < 20000"),
        ({"salary_max": 9000, "currency": "EUR"}, 0,
         "salary in EUR: no minimum set for this currency"),
        ({}, 0, None),
    ],
    ids=["range-reaches-minimum", "only-min", "below-minimum", "unknown-currency", "no-salary"],
)  # fmt: skip
def test_salary(profile, vacancy, fields, points, reason):
    match = score(vacancy(**fields), profile)

    assert match.score == 60 + points
    salary_reasons = [r for r in match.reasons if "salary" in r]
    assert salary_reasons == ([reason] if reason else [])


@pytest.mark.parametrize("remote", [None, "no", "hybrid"])
def test_profile_city_counts_like_remote(profile, vacancy, remote):
    match = score(vacancy(remote=remote, location="Wrocław, Kraków"), profile)

    assert match.score == 60
    assert "+10 location: Wrocław, Kraków" in match.reasons


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        ({"description": "Online casino platform"}, "rejected: minus keywords casino"),
        ({"company": "Betting Corp"}, "rejected: minus keywords betting"),
        ({"requires_polish": True}, "rejected: requires Polish above B1"),
        ({"seniority": "junior"}, "rejected: junior"),
        ({"title": "Junior/Middle QA Engineer", "seniority": None}, "rejected: junior"),
        ({"title": "Senior Python Developer"}, "rejected: not a QA role"),
        ({"title": "Senior Software Engineer in Test"}, "rejected: not a QA role"),
        ({"remote": None, "location": "Warszawa"},
         "rejected: location Warszawa is not remote or Wrocław"),
        ({"remote": "hybrid", "location": None},
         "rejected: location unknown is not remote or Wrocław"),
    ],
    ids=[
        "minus-in-description",
        "minus-in-company",
        "polish",
        "junior-seniority",
        "junior-in-title",
        "not-qa",
        "role-not-in-test-profile",
        "on-site-elsewhere",
        "location-unknown",
    ],
)  # fmt: skip
def test_hard_filters_give_zero(profile, vacancy, fields, reason):
    match = score(vacancy(**{"description": "Playwright and Python", **fields}), profile)

    assert match.score == 0
    assert match.reasons == [reason]


def test_all_rejection_reasons_are_reported(profile, vacancy):
    v = vacancy(title="Junior Developer", requires_polish=True, remote=None, location="Gdańsk")

    assert score(v, profile).reasons == [
        "rejected: requires Polish above B1",
        "rejected: junior",
        "rejected: not a QA role",
        "rejected: location Gdańsk is not remote or Wrocław",
    ]


def test_rejected_vacancy_still_reports_keywords(profile, vacancy):
    match = score(vacancy(requires_polish=True, description="Playwright"), profile)

    assert match.keywords == ["Playwright"]


def test_apply_fills_score_reasons_and_skills_on_a_copy(profile, vacancy):
    original = vacancy(description="Python")

    scored = apply(original, profile)

    assert (scored.score, scored.skills) == (65, ["Python"])
    assert scored.match_reasons == score(original, profile).reasons
    assert original.score is None, "the input vacancy must not be modified"
