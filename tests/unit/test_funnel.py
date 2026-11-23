"""Funnel statistics: counting, the log lines and the GitHub Actions job summary."""

from collections import Counter

import pytest

from job_matcher.funnel import Funnel, write_summary
from job_matcher.matcher import rejection_rule


@pytest.mark.parametrize(
    ("reason", "rule"),
    [
        ("rejected: title says Java", "another stack or area in the title"),
        ("rejected: remote from Київ, Львів only", "remote from Ukraine only"),
        ("rejected: asks for 7+ years of experience", "too many years of experience"),
        ("rejected: salary up to 8000 PLN is below the minimum", "salary below the minimum"),
        ("rejected: location Warszawa is not remote or Wrocław",
         "on-site outside the profile cities"),
        ("rejected: something new", "something new"),  # an unknown filter keeps its text
        ("+20 seniority: senior", None),
    ],
)  # fmt: skip
def test_rejection_rule(reason, rule):
    assert rejection_rule(reason) == rule


@pytest.fixture
def funnel(make_vacancy):
    f = Funnel()
    f.count_collected([make_vacancy(source="dou"), make_vacancy(source="dou"),
                       make_vacancy(source="djinni")])  # fmt: skip
    f.count_rejected(make_vacancy(match_reasons=["rejected: junior", "rejected: not a QA role"]))
    f.count_rejected(make_vacancy(match_reasons=["rejected: junior"]))
    f.below_threshold, f.already_known, f.written, f.sent = 1, 2, 3, 2
    return f


def test_counts(funnel):
    assert funnel.collected == Counter(dou=2, djinni=1)
    assert funnel.rejected == 2, "vacancies, not filters"
    assert funnel.rejected_by == Counter({"junior": 2, "not a QA role": 1})


def test_log_lines(funnel):
    assert funnel.lines() == [
        "collected 3 (dou 2, djinni 1)",
        "rejected 2 (junior 2, not a QA role 1)",
        "below the threshold 1, already known 2",
        "written to the sheet 3, sent to Telegram 2, failed 0",
    ]


def test_empty_funnel_says_none():
    assert Funnel().lines()[:2] == ["collected 0 (none)", "rejected 0 (none)"]


def test_markdown_table(funnel):
    text = funnel.markdown()

    assert "| collected: djinni | 1 |" in text
    assert "| **collected** | **3** |" in text
    assert "| rejected: junior | 2 |" in text
    assert "| **written to the sheet** | **3** |" in text
    assert "| **sent to Telegram** | **2** |" in text


def test_summary_is_appended_to_the_actions_file(funnel, tmp_path):
    path = tmp_path / "summary.md"
    path.write_text("earlier steps\n", encoding="utf-8")

    write_summary(funnel, env={"GITHUB_STEP_SUMMARY": str(path)})

    text = path.read_text(encoding="utf-8")
    assert text.startswith("earlier steps\n## Funnel")


def test_no_summary_outside_actions(funnel, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    write_summary(funnel, env={})

    assert list(tmp_path.iterdir()) == []
