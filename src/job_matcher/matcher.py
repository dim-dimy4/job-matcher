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
from job_matcher.normalize import engagement_months, is_freelance, required_years, us_hours
from job_matcher.profile import Profile

SENIORITY_POINTS = 20  # middle and senior alike
MIDDLE_YEARS = 2  # no grade in the title: asking this many years makes it a middle role
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
# Another language, counted once whether the title or the description names it: -30 when it
# is what the automation is written in, -15 on a hands-on role where it is a nice-to-have,
# nothing when the post offers Python instead ("Java or TypeScript, Python is also fine").
FOREIGN_STACK_POINTS = -30
FOREIGN_STACK_MANUAL_ROLE_POINTS = -15
# Outweighs "Manual" in the title (+30): a manual mobile-app role is still a mobile role.
FOCUS_POINTS = -40
# Places, not words: "performance testing with k6, Gatling or JMeter" in a nice-to-have list
# is one place, and was four "mentions" for a fit. The unwanted areas come back in 3+ places.
FOCUS_PLACES = 3
# In this many places the area is the job: a filter. The fits name one in 2 places at most.
AREA_JOB_PLACES = 4
LOCATION_POINTS = 10
SALARY_POINTS = 10
US_HOURS_POINTS = -20  # a working day set by a US time zone: evenings in Poland
# Remote for named countries without Poland ("Spain", "Cyprus"): usually means living there.
REMOTE_ELSEWHERE_POINTS = -20
SHORT_ENGAGEMENT_MONTHS = 6  # a project this short or shorter is not a job change

_JUNIOR_TITLE = re.compile(r"\bjunior\b", re.IGNORECASE)

REJECTED = "rejected: "
# The start of each hard-filter reason -> its name in the funnel statistics.
_RULES = {
    "minus keywords": "minus keywords",
    "title says": "another stack or area in the title",
    "requires Polish": "Polish above B1",
    "junior": "junior",
    "not a QA role": "not a QA role",
    "location": "on-site outside the profile cities",
    "remote from": "remote from Ukraine only",
    "asks for": "too many years of experience",
    "freelance": "freelance or a short project",
    "a project of": "freelance or a short project",
    "salary": "salary below the minimum",
    "the job is about": "an unwanted area is the job",
}


def rejection_rule(reason: str) -> str | None:
    """'rejected: title says Java' -> 'another stack or area in the title'; None if the
    reason is not a rejection."""
    if not reason.startswith(REJECTED):
        return None
    text = reason.removeprefix(REJECTED)
    return next((name for start, name in _RULES.items() if text.startswith(start)), text)


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


# Sentence and list-item boundaries of a plain-text post.
_PLACE_BREAK = re.compile(r"(?<=[.!?;:])\s+|\n|•|·")


def _places(words: list[str], text: str) -> int:
    """In how many sentences or list items any of the words appears."""
    return sum(bool(_found(words, part)) for part in _PLACE_BREAK.split(text))


def _local_only(v: Vacancy, profile: Profile) -> bool:
    """DOU lists the offices of a remote job: "Київ, Львів" means remote from Ukraine."""
    places = [p.strip() for p in (v.location or "").split(",") if p.strip()]
    return bool(places) and all(_found(profile.local_only_places, p) for p in places)


def _names_other_countries(location: str, profile: Profile) -> bool:
    """A remote job limited to named places, none of them the candidate's. Not when the
    location is empty or just "Remote", nor a DOU list of Ukrainian offices (often with a
    city abroad added), which says where the company sits, not who may apply."""
    if not location.strip() or location.strip().casefold() == "remote":
        return False
    return not _found(profile.local_only_places, location)


def _rejections(v: Vacancy, profile: Profile, text: str) -> list[str]:
    reasons = []
    if minus := _found(profile.minus_keywords, f"{v.company} {text}"):
        reasons.append(f"{REJECTED}minus keywords {', '.join(minus)}")
    for area, words in profile.focus_areas.items():
        if (places := _places(words, text)) >= AREA_JOB_PLACES:
            reasons.append(f"{REJECTED}the job is about {area} ({places} places)")
    if foreign := _found(profile.title_minus_keywords, v.title):
        reasons.append(f"{REJECTED}title says {', '.join(foreign)}")
    if v.requires_polish:
        reasons.append(REJECTED + "requires Polish above B1")
    if v.seniority is Seniority.JUNIOR or _JUNIOR_TITLE.search(v.title):
        reasons.append(REJECTED + "junior")
    if not _found(profile.role_keywords, v.title):
        reasons.append(REJECTED + "not a QA role")
    if v.remote is not RemoteType.YES and not _found(profile.cities, v.location or ""):
        reasons.append(f"{REJECTED}location {v.location or 'unknown'} is not remote or Wrocław")
    elif v.remote is RemoteType.YES and _local_only(v, profile):
        reasons.append(f"{REJECTED}remote from {v.location} only")
    if is_freelance(text):
        reasons.append(REJECTED + "freelance")
    if (months := engagement_months(text)) is not None and months <= SHORT_ENGAGEMENT_MONTHS:
        reasons.append(f"{REJECTED}a project of {months} months")
    years = required_years(text)
    if profile.max_years_required is not None and (years or 0) > profile.max_years_required:
        reasons.append(f"{REJECTED}asks for {years}+ years of experience")
    base = _salary_in_base(v, profile)
    if base is not None and base < profile.salary.minimum:
        reasons.append(f"{REJECTED}{_salary_label(v, base, profile)} is below the minimum")
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
    # A title saying "Manual" or "General QA" is a hybrid role whatever the text repeats:
    # "QA Automation Engineer (20% Automation / 80% Manual Testing)".
    manual_title = _found(profile.manual_title_keywords, v.title)
    hybrid = bool(manual_title) or len(manual) >= MANUAL_PHRASES
    if manual_title:
        rules.append((MANUAL_POINTS, f"hands-on testing: {manual_title[0]} in the title"))
    elif hybrid:
        rules.append((MANUAL_POINTS, f"hands-on testing: {', '.join(manual)}"))
    elif manual:
        rules.append((SOME_MANUAL_POINTS, f"some hands-on testing: {', '.join(manual)}"))
    if not hybrid and (automation := _found(profile.automation_title_keywords, v.title)):
        rules.append((AUTOMATION_TITLE_POINTS, f"automation role: {automation[0]} in the title"))
    framework = _mentions(profile.framework_keywords, text)
    if framework >= FRAMEWORK_MENTIONS:
        rules.append((FRAMEWORK_POINTS, f"framework building: {framework} mentions"))
    for area, words in profile.focus_areas.items():
        if in_title := _found(words, v.title):
            rules.append((FOCUS_POINTS, f"focus on {area}: {in_title[0]} in the title"))
        elif (places := _places(words, text)) >= FOCUS_PLACES:
            rules.append((FOCUS_POINTS, f"focus on {area}: {places} places"))
    rules += _foreign_stack(v, text, profile, hybrid)
    return rules


def _foreign_stack(
    v: Vacancy, text: str, profile: Profile, hands_on_role: bool
) -> list[tuple[int, str]]:
    in_title = _found(profile.title_penalty_keywords, v.title)
    places = _PLACE_BREAK.split(v.description)
    # In the text, a language counts only where Python isn't offered next to it.
    required = [
        w
        for w in _found(profile.foreign_languages, v.description)
        if w not in in_title
        and any(_keyword(w).search(p) and not _keyword("Python").search(p) for p in places)
    ]
    foreign = in_title + required
    if not foreign:
        offered = _found(profile.foreign_languages, text)
        return [(0, f"another stack, Python offered too: {', '.join(offered)}")] if offered else []
    # Named in 3+ places, the language is what the job is written in even on a hands-on
    # role ("Proficiency in Java", "Java-based frameworks"…); in one or two, a nice-to-have.
    if hands_on_role and _places(foreign, v.description) < FOCUS_PLACES:
        return [(FOREIGN_STACK_MANUAL_ROLE_POINTS, f"another stack: {', '.join(foreign)}")]
    return [(FOREIGN_STACK_POINTS, f"automation in another stack: {', '.join(foreign)}")]


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
    elif (years := required_years(text)) is not None and years >= MIDDLE_YEARS:
        rules.append((SENIORITY_POINTS, f"seniority: {years}+ years asked"))
    rules += _work_points(v, text, profile)
    if hours := us_hours(text):
        rules.append((US_HOURS_POINTS, f"US working hours: {hours}"))
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
    elif _names_other_countries(location, profile):
        rules.append((REMOTE_ELSEWHERE_POINTS, f"remote for {location} only"))
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
