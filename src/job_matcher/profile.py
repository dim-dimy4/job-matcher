"""Candidate profile (SPEC §2), loaded from `config/profile.yaml` and validated."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, model_validator

# Relative to the working directory (the repository root), not to this file: once the
# package is installed into site-packages, there is no config/ next to it.
DEFAULT_PROFILE_PATH = Path("config") / "profile.yaml"

# The real salary thresholds are private: the public profile.yaml holds example values,
# and these variables (GitHub Secrets in CI, .env locally) replace them at load time.
SALARY_ENV = {"minimum": "SALARY_MINIMUM", "target": "SALARY_TARGET"}


class SalaryRules(BaseModel):
    """Monthly thresholds in one base currency; other currencies are converted by `rates`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    currency: str = Field(default="PLN", pattern=r"^[A-Z]{3}$")
    minimum: int = Field(default=0, ge=0)  # top of the range below this: rejected
    target: int = Field(default=0, ge=0)  # top of the range at or above this: bonus
    rates: dict[str, PositiveFloat] = Field(default_factory=dict)  # 1 unit = N base units

    @model_validator(mode="after")
    def _check(self) -> SalaryRules:
        if self.target < self.minimum:
            raise ValueError("salary target must not be below the minimum")
        return self

    def to_base(self, amount: int, currency: str) -> int | None:
        """Amount in the base currency, or None if there is no rate for `currency`."""
        if currency == self.currency:
            return amount
        rate = self.rates.get(currency)
        return None if rate is None else round(amount * rate)


class Profile(BaseModel):
    # forbid: a typo in profile.yaml ("minus_keyword") fails loudly instead of silently
    # disabling a filter.
    model_config = ConfigDict(extra="forbid", frozen=True)

    role_keywords: list[str] = Field(min_length=1)
    cities: list[str] = Field(default_factory=list)
    plus_keywords: list[str] = Field(default_factory=list)
    minus_keywords: list[str] = Field(default_factory=list)
    salary: SalaryRules = Field(default_factory=SalaryRules)
    notify_threshold: int = Field(default=70, ge=0, le=100)


def load_profile(path: Path = DEFAULT_PROFILE_PATH, env: Mapping[str, str] = os.environ) -> Profile:
    # safe_load: plain data only, a YAML file can't construct arbitrary Python objects.
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    overrides = {field: env[name] for field, name in SALARY_ENV.items() if env.get(name)}
    if overrides:
        data["salary"] = {**data.get("salary", {}), **overrides}
    return Profile.model_validate(data)
