from collections.abc import Callable
from typing import Any

import pytest

from job_matcher.models import Vacancy

VacancyFactory = Callable[..., Vacancy]


@pytest.fixture
def make_vacancy() -> VacancyFactory:
    """Build a valid Vacancy; each test overrides only the fields it is about."""

    def _make(**overrides: Any) -> Vacancy:
        data: dict[str, Any] = {
            "source": "dou",
            "url": "https://jobs.dou.ua/companies/acme/vacancies/123/",
            "title": "Senior QA Automation Engineer",
            "company": "Acme",
        }
        data.update(overrides)
        return Vacancy(**data)

    return _make
