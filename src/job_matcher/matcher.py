"""Rule-based matching (SPEC §6): a 0-100 score plus the reasons behind it.

Hard filters set the score to 0; otherwise points are added per rule. Every rule that fires
leaves a reason, so a score can always be explained (in Telegram and in tests).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cache

from job_matcher.models import RemoteType, Seniority, Vacancy
from job_matcher.profile import Profile

ROLE_POINTS = 30
SENIORITY_POINTS = 20
KEYWORD_POINTS = 5
MAX_KEYWORD_POINTS = 30
LOCATION_POINTS = 10
SALARY_POINTS = 10

_JUNIOR_TITLE = re.compile(r"\bjunior\b", re.IGNORECASE)


@dataclass(frozen=True)
class Match:
    score: int
    reasons: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)  # plus keywords found in the post


@cache
def _keyword(word: str) -> re.Pattern[str]:
    # Whole word, optional plural: "API" matches "APIs" but not "rapid".
    return re.compile(rf"(?<!\w){re.escape(word)}s?(?!\w)", re.IGNORECASE)


def _found(words: list[str], text: str) -> list[str]:
    return [w for w in words if _keyword(w).search(text)]


def _rejections(v: Vacancy, profile: Profile) -> list[str]:
    reasons = []
    if minus := _found(profile.minus_keywords, f"{v.title} {v.company} {v.description}"):
        reasons.append(f"rejected: minus keywords {', '.join(minus)}")
    if v.requires_polish:
        reasons.append("rejected: requires Polish above B1")
    if v.seniority is Seniority.JUNIOR or _JUNIOR_TITLE.search(v.title):
        reasons.append("rejected: junior")
    if not _found(profile.role_keywords, v.title):
        reasons.append("rejected: not a QA role")
    if v.remote is not RemoteType.YES and not _found(profile.cities, v.location or ""):
        reasons.append(f"rejected: location {v.location or 'unknown'} is not remote or Wrocław")
    base = _salary_in_base(v, profile)
    if base is not None and base < profile.salary.minimum:
        reasons.append(f"rejected: {_salary_label(v, base, profile)} is below the minimum")
    return reasons


def _salary_label(v: Vacancy, base: int | None, profile: Profile) -> str:
    """'up to 5000 USD (18500 PLN), B2B'. Thresholds are never printed: they are private
    and reasons may end up in CI logs."""
    top = v.salary_max if v.salary_max is not None else v.salary_min
    label = f"salary up to {top} {v.currency}"
    if base is not None and v.currency != profile.salary.currency:
        label += f" ({base} {profile.salary.currency})"
    # UoP is quoted gross and B2B net, so the same number means different money. Not
    # filtered yet, only shown.
    if v.contract_type:
        label += f", {v.contract_type}"
    return label


def _salary_in_base(v: Vacancy, profile: Profile) -> int | None:
    """Top of the salary range in the base currency; None if absent or not convertible."""
    top = v.salary_max if v.salary_max is not None else v.salary_min
    if top is None or v.currency is None:
        return None
    return profile.salary.to_base(top, v.currency)


def score(v: Vacancy, profile: Profile) -> Match:
    """Pure function of the vacancy and the profile: easy to test case by case."""
    keywords = _found(profile.plus_keywords, f"{v.title} {v.description}")
    if rejections := _rejections(v, profile):
        return Match(0, rejections, keywords)

    reasons = [f"+{ROLE_POINTS} role: {_found(profile.role_keywords, v.title)[0]}"]
    total = ROLE_POINTS

    if v.seniority in (Seniority.SENIOR, Seniority.LEAD):
        total += SENIORITY_POINTS
        reasons.append(f"+{SENIORITY_POINTS} seniority: {v.seniority}")

    if keywords:
        points = min(KEYWORD_POINTS * len(keywords), MAX_KEYWORD_POINTS)
        total += points
        reasons.append(f"+{points} keywords: {', '.join(keywords)}")

    # Passed the location filter: either remote or in one of the profile's cities.
    total += LOCATION_POINTS
    where = "remote" if v.remote is RemoteType.YES else f"location: {v.location}"
    reasons.append(f"+{LOCATION_POINTS} {where}")

    # Passed the salary filter, so a known salary is at least the minimum.
    if v.currency is not None:
        base = _salary_in_base(v, profile)
        label = _salary_label(v, base, profile)
        if base is None:
            reasons.append(f"{label}: no exchange rate, not compared")
        elif base >= profile.salary.target:
            total += SALARY_POINTS
            reasons.append(f"+{SALARY_POINTS} {label} reaches the target")
        else:
            reasons.append(f"{label} is below the target")

    return Match(total, reasons, keywords)


def apply(v: Vacancy, profile: Profile) -> Vacancy:
    """Return a copy of the vacancy with score, reasons and found skills filled in."""
    match = score(v, profile)
    return v.model_copy(
        update={"score": match.score, "match_reasons": match.reasons, "skills": match.keywords}
    )
