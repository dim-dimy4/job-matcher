"""Matcher rules, table-driven: one vacancy per row, only the fields that matter for the rule.

A test profile is used instead of config/profile.yaml, so editing the real profile never
breaks these tests.
"""

import pytest

from job_matcher.matcher import apply, score
from job_matcher.profile import Profile, SalaryRules


@pytest.fixture
def profile() -> Profile:
    return Profile(
        role_keywords=["QA", "test automation"],
        cities=["Wrocław"],
        remote_locations=["Poland", "за кордоном"],
        local_only_places=["Київ", "Львів"],
        stack_keywords=["Playwright", "Python", "Pytest", "API", "Postman"],
        domain_keywords=["SaaS", "AI", "LLM"],
        manual_keywords=["manual testing", "exploratory", "test case"],
        automation_title_keywords=["AQA", "automation"],
        framework_keywords=["from scratch", "test framework"],
        foreign_languages=["Java", "C#"],
        focus_areas={"mobile": ["mobile", "iOS", "Android"]},
        minus_keywords=["casino", "betting"],
        title_minus_keywords=["ASIC", "Golang", "SDET"],
        max_years_required=6,
        # Round numbers keep the expected values readable: 1 USD = 4 PLN.
        salary=SalaryRules(currency="PLN", minimum=10000, target=15000, rates={"USD": 4.0}),
    )


@pytest.fixture
def vacancy(make_vacancy):
    """A remote-from-Poland Senior QA vacancy with an empty post: seniority + remote = 30."""

    def _make(**overrides):
        data = {"title": "Senior QA Engineer", "remote": "yes", "location": "Poland",
                "seniority": "senior"}  # fmt: skip
        return make_vacancy(**{**data, **overrides})

    return _make


HYBRID = "manual testing, exploratory sessions and test cases"


def test_baseline_score_and_reasons(profile, vacancy):
    match = score(vacancy(), profile)

    assert match.score == 30
    assert match.reasons == ["+20 seniority: senior", "+10 remote: Poland"]


def test_perfect_vacancy_scores_100(profile, vacancy):
    v = vacancy(
        description=f"{HYBRID}. Python, Pytest, Playwright, REST API. SaaS with AI features.",
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
        ("Playwright Python Pytest API Postman", 20, None),  # 5 x 5 capped at 20
    ],
    ids=["two", "case-insensitive", "plural", "not-a-substring", "capped"],
)
def test_stack_keywords(profile, vacancy, description, points, found):
    match = score(vacancy(description=description), profile)

    assert match.score == 30 + points
    if found is not None:
        assert match.keywords == found


def test_domain_keywords_are_capped(profile, vacancy):
    match = score(vacancy(description="A SaaS for LLM and AI teams"), profile)

    assert match.score == 40
    assert "+10 domain: SaaS, AI, LLM" in match.reasons


@pytest.mark.parametrize(
    ("seniority", "expected", "reason"),
    [
        ("senior", 50, "+20 seniority: senior"),
        ("middle", 50, "+20 seniority: middle"),
        ("lead", 15, "-15 lead role"),
        (None, 30, None),
    ],
)
def test_middle_and_senior_score_alike_and_a_lead_loses(profile, vacancy, seniority, expected,
                                                        reason):  # fmt: skip
    match = score(vacancy(seniority=seniority, description="Python Pytest Playwright API"), profile)

    assert match.score == expected
    if reason:
        assert reason in match.reasons


@pytest.mark.parametrize(
    ("description", "points", "reason"),
    [
        (HYBRID, 30, "+30 hands-on testing: manual testing, exploratory, test case"),
        ("some manual testing", 15, "+15 some hands-on testing: manual testing"),
        ("test cases, test cases, test cases", 15, "+15 some hands-on testing: test case"),
        ("reduce manual effort with automation", 0, None),
    ],
    ids=["hybrid", "a-little", "repeats-count-once", "bare-manual-is-not-a-signal"],
)
def test_hands_on_testing(profile, vacancy, description, points, reason):
    match = score(vacancy(description=description), profile)

    assert match.score == 30 + points
    if reason:
        assert reason in match.reasons


@pytest.mark.parametrize(
    ("description", "points"),
    [("", -15), ("some manual testing", 0), (HYBRID, 30)],
    ids=["pure-automation", "a-little-manual", "hybrid-inside"],
)
def test_an_automation_title_needs_hands_on_work(profile, vacancy, description, points):
    match = score(vacancy(title="Senior QA Automation Engineer", description=description), profile)

    assert match.score == 30 + points
    penalized = "-15 automation role: automation in the title" in match.reasons
    assert penalized == (description != HYBRID), "only 3+ hands-on phrases lift the penalty"


@pytest.mark.parametrize(
    ("description", "points", "reason"),
    [
        ("Build a test framework from scratch", -20, "-20 framework building: 2 mentions"),
        ("Maintain the test framework", 0, None),
        ("mobile apps on iOS and Android, mobile-first", -25, "-25 focus on mobile: 4 mentions"),
        ("iOS and Android builds, mobile", 0, None),
        ("Java or C# experience", -15, "-15 another stack: Java, C#"),
        ("JavaScript is a plus", 0, None),
    ],
    ids=["framework", "one-framework-mention", "focus-area", "three-mentions-is-not-focus",
         "foreign-language", "javascript-is-not-java"],
)  # fmt: skip
def test_what_the_job_is_about(profile, vacancy, description, points, reason):
    match = score(vacancy(description=description), profile)

    assert match.score == 30 + points
    if reason:
        assert reason in match.reasons


def test_score_does_not_go_below_zero(profile, vacancy):
    v = vacancy(
        seniority="lead", location=None, description="Java, C#, from scratch, test framework"
    )

    match = score(v, profile)

    assert match.score == 0
    assert not any(r.startswith("rejected") for r in match.reasons), "penalties, not a filter"


@pytest.mark.parametrize(
    ("location", "points", "reason"),
    [
        ("Poland", 10, "+10 remote: Poland"),
        ("Львів, Ряшів (Польща)", 0, "remote, check location: Львів, Ряшів (Польща)"),
        ("за кордоном", 10, "+10 remote: за кордоном"),
        ("Київ, Вроцлав", 0, "remote, check location: Київ, Вроцлав"),
        ("Kyiv, Wrocław", 10, "+10 remote: Kyiv, Wrocław"),
        ("Ukraine", 0, "remote, check location: Ukraine"),
        (None, 0, "remote, check location: not given"),
    ],
    ids=["poland", "not-in-test-profile", "abroad", "city-spelling", "city", "country",
         "no-location"],
)  # fmt: skip
def test_remote_needs_a_location_the_candidate_can_work_from(profile, vacancy, location, points,
                                                             reason):  # fmt: skip
    match = score(vacancy(location=location), profile)

    assert match.score == 20 + points
    assert reason in match.reasons


@pytest.mark.parametrize(
    ("fields", "points", "reason"),
    [
        ({"salary_min": 16000, "salary_max": 20000, "currency": "PLN"}, 10,
         "+10 salary up to 20000 PLN reaches the target"),
        ({"salary_min": 16000, "currency": "PLN"}, 10,
         "+10 salary up to 16000 PLN reaches the target"),
        ({"salary_max": 12000, "currency": "PLN"}, 0, "salary up to 12000 PLN is below the target"),
        ({"salary_max": 10000, "currency": "PLN"}, 0, "salary up to 10000 PLN is below the target"),
        ({"salary_max": 5000, "currency": "USD"}, 10,
         "+10 salary up to 5000 USD (20000 PLN) reaches the target"),
        ({"salary_max": 9000, "currency": "EUR"}, 0,
         "salary up to 9000 EUR: no exchange rate, not compared"),
        ({"salary_max": 20000, "currency": "PLN", "contract_type": "B2B"}, 10,
         "+10 salary up to 20000 PLN, B2B reaches the target"),
        ({}, 0, None),
    ],
    ids=[
        "range-reaches-target",
        "only-min",
        "between-minimum-and-target",
        "exactly-minimum",
        "converted-from-usd",
        "no-rate",
        "contract-type-shown",
        "no-salary",
    ],
)  # fmt: skip
def test_salary(profile, vacancy, fields, points, reason):
    match = score(vacancy(**fields), profile)

    assert match.score == 30 + points
    salary_reasons = [r for r in match.reasons if "salary" in r]
    assert salary_reasons == ([reason] if reason else [])


def test_salary_reasons_never_reveal_thresholds(profile, vacancy):
    reasons = [
        *score(vacancy(salary_max=12000, currency="PLN"), profile).reasons,
        *score(vacancy(salary_max=9000, currency="PLN"), profile).reasons,
    ]

    assert not any("10000" in r or "15000" in r for r in reasons), reasons


@pytest.mark.parametrize("remote", [None, "no", "hybrid"])
def test_profile_city_counts_like_remote(profile, vacancy, remote):
    match = score(vacancy(remote=remote, location="Wrocław, Kraków"), profile)

    assert match.score == 30
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
        ({"title": "Senior QA Automation Engineer, ASIC Simulation"}, "rejected: title says ASIC"),
        ({"title": "QA Engineer - Backend (Golang) Senior"}, "rejected: title says Golang"),
        ({"title": "Senior SDET (QA)"}, "rejected: title says SDET"),
        ({"remote": None, "location": "Warszawa"},
         "rejected: location Warszawa is not remote or Wrocław"),
        ({"remote": "hybrid", "location": None},
         "rejected: location unknown is not remote or Wrocław"),
        ({"location": "Київ, Львів"}, "rejected: remote from Київ, Львів only"),
        ({"description": "Python. 7+ years of experience in QA"},
         "rejected: asks for 7+ years of experience"),
        ({"salary_max": 8000, "currency": "PLN"},
         "rejected: salary up to 8000 PLN is below the minimum"),
        ({"salary_min": 2000, "salary_max": 2400, "currency": "USD"},
         "rejected: salary up to 2400 USD (9600 PLN) is below the minimum"),
    ],
    ids=[
        "minus-in-description",
        "minus-in-company",
        "polish",
        "junior-seniority",
        "junior-in-title",
        "not-qa",
        "role-not-in-test-profile",
        "foreign-domain-in-title",
        "foreign-language-in-title",
        "framework-role-in-title",
        "on-site-elsewhere",
        "location-unknown",
        "remote-from-ukraine-only",
        "too-many-years",
        "salary-below-minimum",
        "converted-salary-below-minimum",
    ],
)  # fmt: skip
def test_hard_filters_give_zero(profile, vacancy, fields, reason):
    match = score(vacancy(**{"description": "Playwright and Python", **fields}), profile)

    assert match.score == 0
    assert match.reasons == [reason]


@pytest.mark.parametrize(
    ("max_years", "description", "rejected"),
    [(6, "6+ years of experience", False), (None, "10+ years of experience", False),
     (6, "a company with 20 years on the market", False)],
    ids=["at-the-limit", "no-limit-set", "not-about-the-candidate"],
)  # fmt: skip
def test_years_filter_edges(profile, vacancy, max_years, description, rejected):
    custom = profile.model_copy(update={"max_years_required": max_years})

    match = score(vacancy(description=description), custom)

    assert (match.score == 0) == rejected


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

    assert (scored.score, scored.skills) == (35, ["Python"])
    assert scored.match_reasons == score(original, profile).reasons
    assert original.score is None, "the input vacancy must not be modified"
