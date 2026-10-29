"""Candidate profile (SPEC §2), loaded from `config/profile.yaml` and validated."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_PROFILE_PATH = Path(__file__).resolve().parents[2] / "config" / "profile.yaml"


class Profile(BaseModel):
    # forbid: a typo in profile.yaml ("minus_keyword") fails loudly instead of silently
    # disabling a filter.
    model_config = ConfigDict(extra="forbid", frozen=True)

    role_keywords: list[str] = Field(min_length=1)
    cities: list[str] = Field(default_factory=list)
    plus_keywords: list[str] = Field(default_factory=list)
    minus_keywords: list[str] = Field(default_factory=list)
    min_monthly_salary: dict[str, int] = Field(default_factory=dict)
    notify_threshold: int = Field(default=70, ge=0, le=100)


def load_profile(path: Path = DEFAULT_PROFILE_PATH) -> Profile:
    # safe_load: plain data only, a YAML file can't construct arbitrary Python objects.
    return Profile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
