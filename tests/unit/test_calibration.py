"""Calibration: the loader and the report on a tiny invented set, and the real profile
against the candidate's own verdicts when that dataset is present.

The real dataset (data/labelled/) is git-ignored: full job posts and private verdicts. In CI
it is absent and those tests are skipped; run them locally before changing the rules.
"""

from pathlib import Path

import pytest

from job_matcher.calibration import LABELLED_DIR, evaluate, load_labelled, report
from job_matcher.matcher import apply
from job_matcher.profile import load_profile

INVENTED_DIR = Path(__file__).parents[1] / "fixtures" / "calibration"

# The real set (verdicts from the sheet's Status: Yes and 50/50 are fit, No is no): every fit
# reaches the sheet; the kinds of no the candidate keeps naming never reach Telegram; and 90%
# of all no's stay out of it. The no's that do reach it are judgement calls rules can't make
# (how deep a TypeScript or Java requirement goes, a pure manual role turned down).
MIN_NO_OUT_OF_TELEGRAM_SHARE = 0.9
NEVER_IN_TELEGRAM = {
    "pure_automation", "ukraine_only", "people_lead", "mobile", "foreign_area",
    "employment_format", "part_time", "location",
}  # fmt: skip


@pytest.fixture(scope="module")
def profile():
    return load_profile(env={})  # example salary thresholds, not the private ones


@pytest.fixture(scope="module")
def invented():
    return load_labelled(INVENTED_DIR)


def test_each_source_goes_through_its_own_parser(invented):
    by_source = {item.vacancy.source: item for item in invented}

    assert set(by_source) == {"dou", "djinni", "himalayas", "jobicy"}
    assert by_source["dou"].vacancy.title == "Senior QA Engineer"
    assert by_source["dou"].vacancy.location == "за кордоном"
    assert by_source["djinni"].vacancy.location == "Europe"
    assert by_source["himalayas"].vacancy.company == "Gamma"
    assert by_source["jobicy"].vacancy.location == "Europe"


def test_excluded_rows_are_skipped(invented):
    assert {item.verdict for item in invented} == {"fit", "maybe", "no"}
    assert len(invented) == 4, "the excluded row has no data file and is not read"


def test_evaluate_counts_fit_passed_and_no_rejected(invented, profile):
    result = evaluate(invented, profile)

    assert result.summary == (
        "fit in the sheet: 1/1, in Telegram: 1/1, no kept out of Telegram: 1/1 (100%), "
        "out of the sheet: 1/1 (100%)"
    )


def test_report_lists_every_vacancy_best_first(invented, profile):
    text = report(invented, profile)

    lines = text.splitlines()
    assert lines[0].startswith("fit ")
    assert "Senior QA Engineer @ Acme" in lines[0]
    assert "[pure_automation]" in text, "a no shows why it is a no"
    assert text.count("\n      ") == len(invented), "one reasons line per vacancy"
    assert text.endswith(evaluate(invented, profile).summary)


needs_real_data = pytest.mark.skipif(
    not (LABELLED_DIR / "labels.csv").exists(), reason="data/labelled/ is local only"
)


@needs_real_data
def test_rules_separate_fit_from_no_on_the_real_verdicts(profile):  # pragma: no cover
    labelled = load_labelled()
    result = evaluate(labelled, profile)

    assert result.fit_in_sheet == result.fit_total, report(labelled, profile)
    share = result.no_out_of_telegram / result.no_total
    assert share >= MIN_NO_OUT_OF_TELEGRAM_SHARE, report(labelled, profile)
    in_telegram = [
        (item.vacancy.company, item.category)
        for item in labelled
        if item.category in NEVER_IN_TELEGRAM
        and (apply(item.vacancy, profile).score or 0) >= profile.notify_threshold
    ]
    assert in_telegram == [], report(labelled, profile)
