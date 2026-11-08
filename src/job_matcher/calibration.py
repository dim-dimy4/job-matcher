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
    fit_passed: int
    fit_total: int
    no_rejected: int
    no_total: int

    @property
    def summary(self) -> str:
        return (
            f"fit passed: {self.fit_passed}/{self.fit_total}, "
            f"no rejected: {self.no_rejected}/{self.no_total} "
            f"({self.no_rejected / self.no_total:.0%})"
        )


def evaluate(labelled: Sequence[Labelled], profile: Profile) -> Result:
    def passes(item: Labelled) -> bool:
        return (apply(item.vacancy, profile).score or 0) >= profile.notify_threshold

    fit = [passes(item) for item in labelled if item.verdict == "fit"]
    no = [passes(item) for item in labelled if item.verdict == "no"]
    return Result(sum(fit), len(fit), no.count(False), len(no))


def report(labelled: Sequence[Labelled], profile: Profile) -> str:
    order = {verdict: i for i, verdict in enumerate(VERDICTS)}
    scored = sorted(
        ((item, apply(item.vacancy, profile)) for item in labelled),
        key=lambda pair: (order[pair[0].verdict], -(pair[1].score or 0)),
    )
    lines = []
    for item, v in scored:
        mark = "pass" if (v.score or 0) >= profile.notify_threshold else "    "
        lines.append(
            f"{item.verdict:<5} {v.score:>3} {mark}  {v.title[:45]} @ {v.company[:20]}"
            + (f"  [{item.category}]" if item.category else "")
        )
        lines.append(f"      {'; '.join(v.match_reasons)}")
    lines.append("")
    lines.append(f"threshold {profile.notify_threshold}: {evaluate(labelled, profile).summary}")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover - a thin CLI over report()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    print(report(load_labelled(), load_profile()))
