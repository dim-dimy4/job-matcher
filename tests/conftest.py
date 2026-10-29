from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from job_matcher.models import Vacancy

FIXTURES_DIR = Path(__file__).parent / "fixtures"

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


@pytest.fixture
def read_fixture() -> Callable[[str], bytes]:
    """Raw bytes of a saved source response, e.g. read_fixture("dou/qa_remote.xml")."""
    return lambda name: (FIXTURES_DIR / name).read_bytes()
