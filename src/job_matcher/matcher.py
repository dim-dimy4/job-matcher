"""Rule-based matching (SPEC §6): a 0-100 score plus the reasons behind it.

Hard filters set the score to 0; otherwise points are added per rule. Every rule that fires
leaves a reason, so a score can always be explained (in Telegram and in tests).

The weights were calibrated on the candidate's own verdicts on 56 vacancies
(`python -m job_matcher.calibration`). What separated fit from no there: how much of the job
is hands-on testing, whether it is about building frameworks, and whether another stack or
area (mobile, load, data, hardware) is the main job. A QA title alone says little: every
vacancy that reaches the matcher has one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cache

from job_matcher.models import RemoteType, Seniority, Vacancy
from job_matcher.normalize import required_years
from job_matcher.profile import Profile

SENIORITY_POINTS = 20  # middle and senior alike
LEAD_POINTS = -15  # no lead role was a fit: they mean managing people, not testing
MANUAL_POINTS = 30  # hands-on testing is a large part of the job
SOME_MANUAL_POINTS = 15
MANUAL_PHRASES = 3  # this many different hands-on phrases: a hybrid role
AUTOMATION_TITLE_POINTS = -15  # an automation title without much hands-on work
FRAMEWORK_POINTS = -20
FRAMEWORK_MENTIONS = 2
STACK_POINTS = 5
MAX_STACK_POINTS = 20
DOMAIN_POINTS = 5
MAX_DOMAIN_POINTS = 10
FOREIGN_LANGUAGE_POINTS = -15
FOCUS_POINTS = -25
FOCUS_MENTIONS = 4
LOCATION_POINTS = 10
SALARY_POINTS = 10

_JUNIOR_TITLE = re.compile(r"\bjunior\b", re.IGNORECASE)


@dataclass(frozen=True)
class Match:
    score: int
    reasons: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)  # the candidate's tools found in the post


@cache
def _keyword(word: str) -> re.Pattern[str]:
    # Whole word, optional plural: "API" matches "APIs" but not "rapid".
    return re.compile(rf"(?<!\w){re.escape(word)}s?(?!\w)", re.IGNORECASE)


def _found(words: list[str], text: str) -> list[str]:
    return [w for w in words if _keyword(w).search(text)]


def _mentions(words: list[str], text: str) -> int:
    return sum(len(_keyword(w).findall(text)) for w in words)


def _local_only(v: Vacancy, profile: Profile) -> bool:
    """DOU lists the offices of a remote job: "Київ, Львів" means remote from Ukraine."""
    places = [p.strip() for p in (v.location or "").split(",") if p.strip()]
    return bool(places) and all(_found(profile.local_only_places, p) for p in places)


def _rejections(v: Vacancy, profile: Profile, text: str) -> list[str]:
    reasons = []
    if minus := _found(profile.minus_keywords, f"{v.company} {text}"):
        reasons.append(f"rejected: minus keywords {', '.join(minus)}")
    if foreign := _found(profile.title_minus_keywords, v.title):
        reasons.append(f"rejected: title says {', '.join(foreign)}")
    if v.requires_polish:
        reasons.append("rejected: requires Polish above B1")
    if v.seniority is Seniority.JUNIOR or _JUNIOR_TITLE.search(v.title):
        reasons.append("rejected: junior")
    if not _found(profile.role_keywords, v.title):
        reasons.append("rejected: not a QA role")
    if v.remote is not RemoteType.YES and not _found(profile.cities, v.location or ""):
        reasons.append(f"rejected: location {v.location or 'unknown'} is not remote or Wrocław")
    elif v.remote is RemoteType.YES and _local_only(v, profile):
        reasons.append(f"rejected: remote from {v.location} only")
    years = required_years(text)
    if profile.max_years_required is not None and (years or 0) > profile.max_years_required:
        reasons.append(f"rejected: asks for {years}+ years of experience")
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


def _capped(points: int, words: list[str], cap: int) -> int:
    return min(points * len(words), cap)


def _work_points(v: Vacancy, text: str, profile: Profile) -> list[tuple[int, str]]:
    """What the job is: hands-on testing, framework building, another area or stack."""
    rules = []
    # Different phrases, not repeats: a post repeating "test cases" is not more hands-on.
    manual = _found(profile.manual_keywords, text)
    if len(manual) >= MANUAL_PHRASES:
        rules.append((MANUAL_POINTS, f"hands-on testing: {', '.join(manual)}"))
    elif manual:
        rules.append((SOME_MANUAL_POINTS, f"some hands-on testing: {', '.join(manual)}"))
    if len(manual) < MANUAL_PHRASES and (
        automation := _found(profile.automation_title_keywords, v.title)
    ):
        rules.append((AUTOMATION_TITLE_POINTS, f"automation role: {automation[0]} in the title"))
    framework = _mentions(profile.framework_keywords, text)
    if framework >= FRAMEWORK_MENTIONS:
        rules.append((FRAMEWORK_POINTS, f"framework building: {framework} mentions"))
    for area, words in profile.focus_areas.items():
        mentions = _mentions(words, text)
        if mentions >= FOCUS_MENTIONS:
            rules.append((FOCUS_POINTS, f"focus on {area}: {mentions} mentions"))
    if languages := _found(profile.foreign_languages, text):
        rules.append((FOREIGN_LANGUAGE_POINTS, f"another stack: {', '.join(languages)}"))
    return rules


def score(v: Vacancy, profile: Profile) -> Match:
    """Pure function of the vacancy and the profile: easy to test case by case."""
    text = f"{v.title}\n{v.description}"
    stack = _found(profile.stack_keywords, text)
    if rejections := _rejections(v, profile, text):
        return Match(0, rejections, stack)

    rules: list[tuple[int, str]] = []
    if v.seniority in (Seniority.MIDDLE, Seniority.SENIOR):
        rules.append((SENIORITY_POINTS, f"seniority: {v.seniority}"))
    elif v.seniority is Seniority.LEAD:
        rules.append((LEAD_POINTS, "lead role"))
    rules += _work_points(v, text, profile)
    if stack:
        points = _capped(STACK_POINTS, stack, MAX_STACK_POINTS)
        rules.append((points, f"stack: {', '.join(stack)}"))
    if domain := _found(profile.domain_keywords, text):
        points = _capped(DOMAIN_POINTS, domain, MAX_DOMAIN_POINTS)
        rules.append((points, f"domain: {', '.join(domain)}"))

    # Passed the location filter: either remote or in one of the profile's cities. "Remote"
    # alone often means remote from Ukraine (DOU), so it earns points only when the post
    # names a place the candidate can work from.
    location = v.location or ""
    reasons = []
    if v.remote is not RemoteType.YES:
        rules.append((LOCATION_POINTS, f"location: {location}"))
    elif _found(profile.remote_locations + profile.cities, location):
        rules.append((LOCATION_POINTS, f"remote: {location}"))
    else:
        reasons.append(f"remote, check location: {location or 'not given'}")

    # Passed the salary filter, so a known salary is at least the minimum.
    if v.currency is not None:
        base = _salary_in_base(v, profile)
        label = _salary_label(v, base, profile)
        if base is None:
            reasons.append(f"{label}: no exchange rate, not compared")
        elif base >= profile.salary.target:
            rules.append((SALARY_POINTS, f"{label} reaches the target"))
        else:
            reasons.append(f"{label} is below the target")

    total = max(0, min(100, sum(points for points, _ in rules)))
    return Match(total, [f"{points:+d} {why}" for points, why in rules] + reasons, stack)


def apply(v: Vacancy, profile: Profile) -> Vacancy:
    """Return a copy of the vacancy with score, reasons and found skills filled in."""
    match = score(v, profile)
    return v.model_copy(
        update={"score": match.score, "match_reasons": match.reasons, "skills": match.keywords}
    )
