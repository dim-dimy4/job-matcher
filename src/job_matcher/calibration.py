"""Calibration on the candidate's own verdicts: how well the score separates fit from no.

56 vacancies the bot sent were marked by hand (fit / maybe / no). Their source data is
frozen in data/labelled/ (job posts disappear within weeks, so it can't be fetched again)
and goes through the same parsers as a live run. The folder is git-ignored: it holds full
job posts, which a public repository shouldn't redistribute, and private verdicts.

python -m job_matcher.calibration   # the table and the two numbers below
"""

from __future__ import annotations

import csv
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element, SubElement

from job_matcher.collectors import djinni, dou, himalayas
from job_matcher.matcher import apply
from job_matcher.models import Vacancy
from job_matcher.profile import Profile, load_profile

LABELLED_DIR = Path("data") / "labelled"
VERDICTS = ("fit", "maybe", "no")  # "excluded" rows (post gone before marking) are skipped


@dataclass(frozen=True)
class Labelled:
    vacancy: Vacancy
    verdict: str
    category: str  # why a "no" is a no, e.g. "pure_automation"


def _dou(raw: dict[str, str]) -> Vacancy:
    item = Element("item")
    for tag, text in raw.items():
        SubElement(item, tag).text = text
    return dou.parse_item(item)


def _djinni(raw: dict[str, Any], url: str) -> Vacancy:
    item = djinni.FeedItem(url, raw["title"], datetime.fromisoformat(raw["published_at"]))
    return djinni.parse_job(raw["job"], item)


def load_labelled(directory: Path = LABELLED_DIR) -> list[Labelled]:
    with (directory / "labels.csv").open(encoding="utf-8", newline="") as f:
        labels = [row for row in csv.DictReader(f) if row["verdict"] in VERDICTS]
    result = []
    for label in labels:
        path = directory / f"{label['source']}_{label['id']}.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        if label["source"] == "dou":
            vacancy = _dou(raw)
        elif label["source"] == "djinni":
            vacancy = _djinni(raw, label["url"])
        else:
            vacancy = himalayas.parse_job(raw)
        result.append(Labelled(vacancy, label["verdict"], label["category"]))
    return result


@dataclass(frozen=True)
class Result:
    """Two thresholds, two questions: does every fit reach the sheet, and does a no stay
    out of Telegram (and how many stay out of the sheet too)."""

    fit_in_sheet: int
    fit_total: int
    no_out_of_telegram: int
    no_out_of_sheet: int
    no_total: int

    @property
    def summary(self) -> str:
        return (
            f"fit in the sheet: {self.fit_in_sheet}/{self.fit_total}, "
            f"no kept out of Telegram: {self.no_out_of_telegram}/{self.no_total} "
            f"({self.no_out_of_telegram / self.no_total:.0%}), "
            f"out of the sheet: {self.no_out_of_sheet}/{self.no_total} "
            f"({self.no_out_of_sheet / self.no_total:.0%})"
        )


def evaluate(labelled: Sequence[Labelled], profile: Profile) -> Result:
    def scores(verdict: str) -> list[int]:
        return [apply(i.vacancy, profile).score or 0 for i in labelled if i.verdict == verdict]

    fit, no = scores("fit"), scores("no")
    return Result(
        fit_in_sheet=sum(s >= profile.sheet_threshold for s in fit),
        fit_total=len(fit),
        no_out_of_telegram=sum(s < profile.notify_threshold for s in no),
        no_out_of_sheet=sum(s < profile.sheet_threshold for s in no),
        no_total=len(no),
    )


def _destination(score: int, profile: Profile) -> str:
    if score >= profile.notify_threshold:
        return "telegram"
    return "sheet" if score >= profile.sheet_threshold else ""


def report(labelled: Sequence[Labelled], profile: Profile) -> str:
    order = {verdict: i for i, verdict in enumerate(VERDICTS)}
    scored = sorted(
        ((item, apply(item.vacancy, profile)) for item in labelled),
        key=lambda pair: (order[pair[0].verdict], -(pair[1].score or 0)),
    )
    lines = []
    for item, v in scored:
        mark = _destination(v.score or 0, profile)
        lines.append(
            f"{item.verdict:<5} {v.score:>3} {mark:<8}  {v.title[:45]} @ {v.company[:20]}"
            + (f"  [{item.category}]" if item.category else "")
        )
        lines.append(f"      {'; '.join(v.match_reasons)}")
    lines.append("")
    lines.append(
        f"sheet {profile.sheet_threshold}, Telegram {profile.notify_threshold}: "
        f"{evaluate(labelled, profile).summary}"
    )
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover - a thin CLI over report()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    print(report(load_labelled(), load_profile()))
