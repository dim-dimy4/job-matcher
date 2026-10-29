from datetime import datetime

import pytest
from pydantic import ValidationError

from job_matcher.models import RemoteType, Source

URL = "https://jobs.dou.ua/companies/acme/vacancies/123/"


def test_minimal_vacancy_has_safe_defaults(make_vacancy):
    v = make_vacancy()

    assert v.source is Source.DOU
    assert v.requires_polish is False
    assert v.skills == []
    assert v.match_reasons == []
    assert v.score is None
    assert v.collected_at.tzinfo is not None


def test_id_is_stable_for_same_url(make_vacancy):
    first = make_vacancy(url=URL, title="QA Lead")
    second = make_vacancy(url=URL, title="Renamed later")

    assert first.id == second.id
    assert len(first.id) == 16
    assert set(first.id) <= set("0123456789abcdef")


def test_id_differs_for_different_urls(make_vacancy):
    assert make_vacancy(url=URL).id != make_vacancy(url=URL.replace("123", "124")).id


def test_id_is_included_in_serialized_output(make_vacancy):
    v = make_vacancy()

    assert v.model_dump()["id"] == v.id


def test_string_enums_are_parsed(make_vacancy):
    assert make_vacancy(remote="hybrid").remote is RemoteType.HYBRID


@pytest.mark.parametrize("score", [0, 70, 100])
def test_score_accepts_boundaries(make_vacancy, score):
    assert make_vacancy(score=score).score == score


@pytest.mark.parametrize("score", [-1, 101])
def test_score_out_of_range_rejected_on_create(make_vacancy, score):
    with pytest.raises(ValidationError, match="score"):
        make_vacancy(score=score)


def test_score_out_of_range_rejected_on_assignment(make_vacancy):
    v = make_vacancy()

    with pytest.raises(ValidationError, match="score"):
        v.score = 150


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"salary_min": 20000, "salary_max": 10000, "currency": "PLN"}, "must not exceed"),
        ({"salary_min": 20000}, "currency is required"),
        ({"salary_min": 1, "currency": "pln"}, "currency"),
        ({"salary_min": -1, "currency": "PLN"}, "salary_min"),
    ],
    ids=["min-gt-max", "no-currency", "lowercase-currency", "negative"],
)
def test_invalid_salary_rejected(make_vacancy, overrides, error):
    with pytest.raises(ValidationError, match=error):
        make_vacancy(**overrides)


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"url": "not-a-url"}, "url"),
        ({"title": "   "}, "title"),
        ({"source": "linkedin"}, "source"),
        ({"remote": "maybe"}, "remote"),
        ({"published_at": datetime(2026, 10, 1)}, "published_at"),  # naive datetime
        ({"salaryMin": 100}, "salaryMin"),  # unknown field
    ],
    ids=["bad-url", "blank-title", "unknown-source", "bad-remote", "naive-dt", "extra-field"],
)
def test_invalid_fields_rejected(make_vacancy, overrides, field):
    with pytest.raises(ValidationError, match=field):
        make_vacancy(**overrides)
