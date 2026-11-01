"""The real config/profile.yaml must stay valid; typos in it must fail loudly."""

import pytest
from pydantic import ValidationError

from job_matcher.profile import Profile, SalaryRules, load_profile


def test_real_profile_loads():
    profile = load_profile(env={})

    assert "QA" in profile.role_keywords
    assert 0 <= profile.notify_threshold <= 100


def test_default_profile_is_found_from_the_working_directory(tmp_path, monkeypatch):
    # The scheduled run uses a regular (not editable) install: the package sits in
    # site-packages, and config/ is only in the checked-out repository.
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "profile.yaml").write_text("role_keywords: [SDET]\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert load_profile(env={}).role_keywords == ["SDET"]


def test_profile_from_custom_path(tmp_path):
    path = tmp_path / "profile.yaml"
    path.write_text("role_keywords: [QA]\nnotify_threshold: 50\n", encoding="utf-8")

    assert load_profile(path, env={}) == Profile(role_keywords=["QA"], notify_threshold=50)


@pytest.mark.parametrize(
    "data",
    [
        {"role_keywords": ["QA"], "minus_keyword": ["casino"]},  # typo in the key
        {"role_keywords": []},
        {"role_keywords": ["QA"], "notify_threshold": 120},
        {"role_keywords": ["QA"], "salary": {"minimum": "a lot"}},
        {"role_keywords": ["QA"], "salary": {"minimum": 2000, "target": 1000}},
        {"role_keywords": ["QA"], "salary": {"rates": {"USD": 0}}},
        {"role_keywords": ["QA"], "salary": {"currency": "zloty"}},
    ],
    ids=[
        "unknown-key",
        "no-roles",
        "threshold-over-100",
        "salary-not-a-number",
        "target-below-minimum",
        "zero-rate",
        "bad-currency",
    ],
)
def test_invalid_profile_is_rejected(data):
    with pytest.raises(ValidationError):
        Profile.model_validate(data)


@pytest.fixture
def profile_file(tmp_path):
    path = tmp_path / "profile.yaml"
    path.write_text(
        "role_keywords: [QA]\n"
        "salary: {currency: PLN, minimum: 1000, target: 2000, rates: {USD: 4}}\n",
        encoding="utf-8",
    )
    return path


def test_salary_comes_from_environment(profile_file):
    env = {"SALARY_MINIMUM": "11000", "SALARY_TARGET": "17000"}

    salary = load_profile(profile_file, env=env).salary

    assert (salary.minimum, salary.target) == (11000, 17000)
    assert salary.rates == {"USD": 4}, "fields not overridden stay from the file"


def test_empty_environment_value_keeps_the_file_value(profile_file):
    env = {"SALARY_MINIMUM": "", "SALARY_TARGET": "3000"}

    salary = load_profile(profile_file, env=env).salary

    assert (salary.minimum, salary.target) == (1000, 3000)


def test_invalid_environment_value_is_rejected(profile_file):
    with pytest.raises(ValidationError):
        load_profile(profile_file, env={"SALARY_MINIMUM": "fourteen thousand"})


@pytest.mark.parametrize(
    ("amount", "currency", "expected"),
    [(5000, "PLN", 5000), (5000, "USD", 18500), (5000, "EUR", None)],
    ids=["base-currency", "converted", "no-rate"],
)
def test_salary_to_base(amount, currency, expected):
    rules = SalaryRules(currency="PLN", rates={"USD": 3.7})

    assert rules.to_base(amount, currency) == expected
