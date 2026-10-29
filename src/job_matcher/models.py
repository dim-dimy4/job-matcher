"""Unified vacancy model (SPEC §5). Every collector normalizes into `Vacancy`."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    computed_field,
    model_validator,
)


class Source(StrEnum):
    HIMALAYAS = "himalayas"
    DOU = "dou"


class RemoteType(StrEnum):
    YES = "yes"
    NO = "no"
    HYBRID = "hybrid"


class Seniority(StrEnum):
    JUNIOR = "junior"
    MIDDLE = "middle"
    SENIOR = "senior"
    LEAD = "lead"


class ContractType(StrEnum):
    B2B = "B2B"
    UOP = "UoP"


class Language(StrEnum):
    EN = "en"
    PL = "pl"
    UK = "uk"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Vacancy(BaseModel):
    # forbid: a typo in a collector's field name fails loudly instead of being dropped.
    # validate_assignment: the matcher setting score=150 later is also rejected.
    model_config = ConfigDict(extra="forbid", validate_assignment=True, str_strip_whitespace=True)

    source: Source
    url: HttpUrl
    title: str = Field(min_length=1)
    company: str = Field(min_length=1)

    location: str | None = None
    remote: RemoteType | None = None
    seniority: Seniority | None = None

    salary_min: int | None = Field(default=None, ge=0)
    salary_max: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    contract_type: ContractType | None = None

    language_of_post: Language | None = None
    requires_polish: bool = False
    # Plain text of the post; the matcher looks for keywords here. Not stored in the sheet.
    description: str = ""

    skills: list[str] = Field(default_factory=list)
    published_at: AwareDatetime | None = None
    collected_at: AwareDatetime = Field(default_factory=_utcnow)

    score: int | None = Field(default=None, ge=0, le=100)
    match_reasons: list[str] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def id(self) -> str:
        """Stable dedup key: derived from the URL, so the same posting always gets the same id."""
        return hashlib.sha256(str(self.url).encode()).hexdigest()[:16]

    @model_validator(mode="after")
    def _check_salary(self) -> Vacancy:
        has_salary = self.salary_min is not None or self.salary_max is not None
        if has_salary and self.currency is None:
            raise ValueError("currency is required when salary is set")
        if (
            self.salary_min is not None
            and self.salary_max is not None
            and self.salary_min > self.salary_max
        ):
            raise ValueError("salary_min must not exceed salary_max")
        return self
