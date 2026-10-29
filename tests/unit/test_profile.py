"""The real config/profile.yaml must stay valid; typos in it must fail loudly."""

import pytest
from pydantic import ValidationError

from job_matcher.profile import Profile, load_profile


def test_real_profile_loads():
    profile = load_profile()

    assert "QA" in profile.role_keywords
    assert 0 <= profile.notify_threshold <= 100


def test_profile_from_custom_path(tmp_path):
    path = tmp_path / "profile.yaml"
    path.write_text("role_keywords: [QA]\nnotify_threshold: 50\n", encoding="utf-8")

    assert load_profile(path) == Profile(role_keywords=["QA"], notify_threshold=50)


@pytest.mark.parametrize(
    "data",
    [
        {"role_keywords": ["QA"], "minus_keyword": ["casino"]},  # typo in the key
        {"role_keywords": []},
        {"role_keywords": ["QA"], "notify_threshold": 120},
        {"role_keywords": ["QA"], "min_monthly_salary": {"PLN": "a lot"}},
    ],
    ids=["unknown-key", "no-roles", "threshold-over-100", "salary-not-a-number"],
)
def test_invalid_profile_is_rejected(data):
    with pytest.raises(ValidationError):
        Profile.model_validate(data)
