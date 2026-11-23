"""Funnel statistics of one run: where the collected vacancies went.

Printed to the log and, in GitHub Actions, to the job summary ($GITHUB_STEP_SUMMARY), so
"why so few vacancies today?" is answered by the run page itself.
"""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from job_matcher.matcher import rejection_rule
from job_matcher.models import Vacancy

# Stages counted per source, after "collected"; "written" includes the ones also sent.
SOURCE_STAGES = ("rejected", "below", "known", "written", "sent")


@dataclass
class Funnel:
    collected: Counter[str] = field(default_factory=Counter)  # by source
    # By hard filter. A vacancy failing several filters counts under each of them.
    rejected_by: Counter[str] = field(default_factory=Counter)
    rejected: int = 0
    below_threshold: int = 0
    already_known: int = 0  # in the sheet already, or a duplicate from another board
    written: int = 0  # new rows in the sheet
    sheet_only: int = 0  # of them, below the Telegram threshold
    sent: int = 0
    failed: int = 0
    # (source, stage) -> vacancies: which board feeds the sheet and which only adds noise.
    by_source: Counter[tuple[str, str]] = field(default_factory=Counter)

    def count_collected(self, vacancies: Iterable[Vacancy]) -> None:
        self.collected.update(v.source for v in vacancies)

    def count_rejected(self, v: Vacancy) -> None:
        self.rejected += 1
        self.rejected_by.update(
            rule for r in v.match_reasons if (rule := rejection_rule(r)) is not None
        )
        self.note("rejected", v)

    def note(self, stage: str, v: Vacancy) -> None:
        """Count one vacancy at a stage of its source (one of SOURCE_STAGES)."""
        self.by_source[(v.source, stage)] += 1

    def source_lines(self) -> list[str]:
        return [
            f"{source}: collected {n}, "
            + ", ".join(f"{stage} {self.by_source[(source, stage)]}" for stage in SOURCE_STAGES)
            for source, n in self.collected.most_common()
        ]

    def lines(self) -> list[str]:
        return [
            f"collected {self.collected.total()} ({_breakdown(self.collected)})",
            f"rejected {self.rejected} ({_breakdown(self.rejected_by)})",
            f"below the sheet threshold {self.below_threshold}, already known {self.already_known}",
            f"written to the sheet {self.written} ({self.sheet_only} below the Telegram "
            f"threshold), sent to Telegram {self.sent}, failed {self.failed}",
            *self.source_lines(),
        ]

    def markdown(self) -> str:
        rows = [
            "## Funnel",
            "",
            "| Stage | Vacancies |",
            "|---|---:|",
            *(f"| collected: {source} | {n} |" for source, n in sorted(self.collected.items())),
            f"| **collected** | **{self.collected.total()}** |",
            *(f"| rejected: {rule} | {n} |" for rule, n in self.rejected_by.most_common()),
            f"| **rejected** | **{self.rejected}** |",
            f"| below the sheet threshold | {self.below_threshold} |",
            f"| already known | {self.already_known} |",
            f"| **written to the sheet** | **{self.written}** |",
            f"| of them below the Telegram threshold | {self.sheet_only} |",
            f"| **sent to Telegram** | **{self.sent}** |",
            f"| failed to send | {self.failed} |",
            "",
            "A vacancy failing several filters counts under each of them; the bold rejected",
            "row counts vacancies.",
            "",
            "### By source",
            "",
            "| Source | Collected | " + " | ".join(s.capitalize() for s in SOURCE_STAGES) + " |",
            "|---|" + "---:|" * (len(SOURCE_STAGES) + 1),
            *(
                f"| {source} | {n} | "
                + " | ".join(str(self.by_source[(source, stage)]) for stage in SOURCE_STAGES)
                + " |"
                for source, n in self.collected.most_common()
            ),
            "",
        ]
        return "\n".join(rows)


def _breakdown(counts: Mapping[str, int]) -> str:
    return ", ".join(f"{name} {n}" for name, n in Counter(counts).most_common()) or "none"


def write_summary(funnel: Funnel, env: Mapping[str, str] = os.environ) -> None:
    """Append the funnel to the GitHub Actions job summary; outside Actions, do nothing."""
    if path := env.get("GITHUB_STEP_SUMMARY"):
        with Path(path).open("a", encoding="utf-8") as f:
            f.write(funnel.markdown())
