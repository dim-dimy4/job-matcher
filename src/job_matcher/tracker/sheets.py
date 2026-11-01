"""The "Job Tracker" Google Sheet (SPEC §7): the vacancy log and the dedup memory.

GitHub Actions starts every run on a clean machine, so a local SQLite file would be lost
between runs. The sheet already keeps every vacancy the bot has sent, so it doubles as the
list of seen vacancies: one source of truth, no extra state to store.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from enum import StrEnum
from typing import Any, Protocol

import gspread
from gspread.utils import rowcol_to_a1

from job_matcher.models import RemoteType, Vacancy, url_id

HEADER = [
    "Date", "Source", "Company", "Vacancy", "Link", "Location/format", "Salary range",
    "Score", "Reasons", "Status", "Resume version", "Applied on", "Response on", "HR",
    "Test task", "Tech", "Rejection reason", "Notes",
]  # fmt: skip
COMPANY, VACANCY, LINK = HEADER.index("Company"), HEADER.index("Vacancy"), HEADER.index("Link")
STATUS, APPLIED_ON = HEADER.index("Status"), HEADER.index("Applied on")
NEW_STATUS = "New"
APPLIED, SKIPPED = "Applied", "Skipped"
# The statuses a Telegram button may overwrite. Later ones (HR, Offer…) are set by hand in
# the sheet, and a late or repeated press must not roll them back.
BUTTON_STATUSES = (APPLIED, SKIPPED)
_REPLACEABLE = {"", NEW_STATUS, *BUTTON_STATUSES}

# Reading and appending rows of one spreadsheet, nothing more (no access to the rest of Drive).
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class Worksheet(Protocol):
    """The gspread calls the tracker uses; tests pass an in-memory fake."""

    def get_all_values(self) -> list[list[str]]: ...

    def append_rows(
        self, values: Sequence[Sequence[str | int]], value_input_option: Any
    ) -> Any: ...

    def batch_update(self, data: Sequence[Mapping[str, Any]], value_input_option: Any) -> Any: ...


def open_worksheet(service_account: Mapping[str, Any], sheet_id: str) -> Worksheet:
    client = gspread.service_account_from_dict(service_account, scopes=SCOPES)
    return client.open_by_key(sheet_id).sheet1


_NON_WORD = re.compile(r"\W+")


def same_job_key(company: str, title: str) -> str:
    """One posting on two boards has two URLs but the same company and title."""
    return "|".join(_NON_WORD.sub(" ", part).casefold().strip() for part in (company, title))


class KnownVacancies:
    """Seen vacancies, matched by URL or by company + title."""

    def __init__(self, rows: Iterable[Sequence[str]] = ()) -> None:
        self._urls: set[str] = set()
        self._jobs: set[str] = set()
        for row in rows:
            if len(row) > LINK:
                self._urls.add(row[LINK])
                self._jobs.add(same_job_key(row[COMPANY], row[VACANCY]))

    def __contains__(self, v: Vacancy) -> bool:
        return str(v.url) in self._urls or same_job_key(v.company, v.title) in self._jobs

    def add(self, v: Vacancy) -> None:
        self._urls.add(str(v.url))
        self._jobs.add(same_job_key(v.company, v.title))


def salary_range(v: Vacancy) -> str:
    if v.salary_min is not None and v.salary_max is not None:
        text = f"{v.salary_min}-{v.salary_max} {v.currency}"
    elif v.salary_min is not None:
        text = f"from {v.salary_min} {v.currency}"
    elif v.salary_max is not None:
        text = f"up to {v.salary_max} {v.currency}"
    else:
        return ""
    return f"{text}, {v.contract_type}" if v.contract_type else text


_FORMAT = {RemoteType.YES: "remote", RemoteType.NO: "on-site", RemoteType.HYBRID: "hybrid"}


def location_format(v: Vacancy) -> str:
    work_format = _FORMAT[v.remote] if v.remote else None
    return " · ".join(part for part in (v.location, work_format) if part)


def to_row(v: Vacancy) -> list[str | int]:
    """The bot fills the first ten columns; the rest are for manual tracking."""
    return [
        v.collected_at.date().isoformat(),
        v.source,
        v.company,
        v.title,
        str(v.url),
        location_format(v),
        salary_range(v),
        v.score if v.score is not None else "",
        "; ".join(v.match_reasons),
        NEW_STATUS,
    ]


class StatusChange(StrEnum):
    SET = "set"
    UNCHANGED = "unchanged"  # the same button pressed again
    KEPT = "kept"  # the row is further down the funnel than a button can set
    NOT_FOUND = "not found"


class SheetTracker:
    def __init__(self, worksheet: Worksheet) -> None:
        self._worksheet = worksheet
        rows = worksheet.get_all_values()
        # A new, empty sheet comes back as [[]], not []: one row with no cells.
        self._has_header = bool(rows and any(rows[0]))
        self.known = KnownVacancies(rows[1:])
        # Row numbers by vacancy id: rows are only ever appended, so they stay valid.
        self._rows = {
            url_id(row[LINK]): (n, row) for n, row in enumerate(rows, 1) if len(row) > LINK
        }

    def set_status(self, vacancy_id: str, status: str, today: date) -> StatusChange:
        """Status from a button. "Applied" also fills "Applied on" unless it is already set;
        "Skipped" clears it, so a mistaken "Applied" can be taken back."""
        if vacancy_id not in self._rows:
            return StatusChange.NOT_FOUND
        n, row = self._rows[vacancy_id]
        row = row + [""] * (len(HEADER) - len(row))  # trailing empty cells are not returned
        if row[STATUS] == status:
            return StatusChange.UNCHANGED
        if row[STATUS] not in _REPLACEABLE:
            return StatusChange.KEPT
        applied_on = (row[APPLIED_ON] or today.isoformat()) if status == APPLIED else ""
        self._worksheet.batch_update(
            [
                {"range": rowcol_to_a1(n, STATUS + 1), "values": [[status]]},
                {"range": rowcol_to_a1(n, APPLIED_ON + 1), "values": [[applied_on]]},
            ],
            value_input_option="RAW",
        )
        row[STATUS], row[APPLIED_ON] = status, applied_on
        self._rows[vacancy_id] = (n, row)
        return StatusChange.SET

    def append(self, vacancies: Sequence[Vacancy]) -> None:
        rows = [to_row(v) for v in vacancies]
        if not self._has_header:
            rows.insert(0, list(HEADER))
        if not rows:
            return
        # RAW: values are stored as typed. With USER_ENTERED a title like "=HYPERLINK(...)"
        # from a job board would run as a formula in the sheet (formula injection).
        self._worksheet.append_rows(rows, value_input_option="RAW")
        self._has_header = True
        for v in vacancies:
            self.known.add(v)
