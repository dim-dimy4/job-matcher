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

# The header the bot writes on an empty sheet. Columns are found by name, not position:
# adding, removing or moving a column by hand in the sheet breaks nothing.
HEADER = [
    "Date", "Source", "Company", "Vacancy", "Link", "Location/format", "Salary range",
    "Score", "Reasons", "Status", "Comment", "Resume version", "Applied on", "Response on",
    "HR", "Test task", "Tech", "Rejection reason", "Notes",
]  # fmt: skip
WRITTEN = HEADER[: HEADER.index("Status") + 1]  # filled by the bot for every new vacancy
REQUIRED_COLUMNS = (*WRITTEN, "Applied on")
DEFAULT_COLUMNS = {name: i for i, name in enumerate(HEADER)}
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


class SheetError(ValueError):
    pass


def find_columns(header: Sequence[str]) -> dict[str, int]:
    """Column positions by name. A missing column the bot writes is an error: guessing its
    place would put values under the wrong heading."""
    columns = {name.strip(): i for i, name in enumerate(header) if name.strip()}
    if missing := [name for name in REQUIRED_COLUMNS if name not in columns]:
        raise SheetError(f"the sheet header has no column(s): {', '.join(missing)}")
    return columns


_NON_WORD = re.compile(r"\W+")


def same_job_key(company: str, title: str) -> str:
    """One posting on two boards has two URLs but the same company and title."""
    return "|".join(_NON_WORD.sub(" ", part).casefold().strip() for part in (company, title))


class KnownVacancies:
    """Seen vacancies, matched by URL or by company + title."""

    def __init__(
        self, rows: Iterable[Sequence[str]] = (), columns: Mapping[str, int] = DEFAULT_COLUMNS
    ) -> None:
        self._urls: set[str] = set()
        self._jobs: set[str] = set()
        company, title, link = columns["Company"], columns["Vacancy"], columns["Link"]
        for row in rows:
            if len(row) > max(company, title, link):
                self._urls.add(row[link])
                self._jobs.add(same_job_key(row[company], row[title]))

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


def to_row(v: Vacancy) -> dict[str, str | int]:
    """The values the bot fills, by column name; the other columns are for manual tracking."""
    return {
        "Date": v.collected_at.date().isoformat(),
        "Source": v.source,
        "Company": v.company,
        "Vacancy": v.title,
        "Link": str(v.url),
        "Location/format": location_format(v),
        "Salary range": salary_range(v),
        "Score": v.score if v.score is not None else "",
        "Reasons": "; ".join(v.match_reasons),
        "Status": NEW_STATUS,
    }


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
        self._columns = find_columns(rows[0]) if self._has_header else DEFAULT_COLUMNS
        self.known = KnownVacancies(rows[1:], self._columns)
        # Row numbers by vacancy id: rows are only ever appended, so they stay valid.
        link = self._columns["Link"]
        self._rows = {
            url_id(row[link]): (n, row) for n, row in enumerate(rows, 1) if len(row) > link
        }

    def set_status(self, vacancy_id: str, status: str, today: date) -> StatusChange:
        """Status from a button. "Applied" also fills "Applied on" unless it is already set;
        "Skipped" clears it, so a mistaken "Applied" can be taken back."""
        if vacancy_id not in self._rows:
            return StatusChange.NOT_FOUND
        n, row = self._rows[vacancy_id]
        status_at, applied_at = self._columns["Status"], self._columns["Applied on"]
        row = row + [""] * (max(status_at, applied_at) + 1 - len(row))  # trailing empties
        if row[status_at] == status:
            return StatusChange.UNCHANGED
        if row[status_at] not in _REPLACEABLE:
            return StatusChange.KEPT
        applied_on = (row[applied_at] or today.isoformat()) if status == APPLIED else ""
        self._worksheet.batch_update(
            [
                {"range": rowcol_to_a1(n, status_at + 1), "values": [[status]]},
                {"range": rowcol_to_a1(n, applied_at + 1), "values": [[applied_on]]},
            ],
            value_input_option="RAW",
        )
        row[status_at], row[applied_at] = status, applied_on
        self._rows[vacancy_id] = (n, row)
        return StatusChange.SET

    def _layout(self, values: Mapping[str, str | int]) -> list[str | int]:
        row: list[str | int] = [""] * (max(self._columns[name] for name in values) + 1)
        for name, value in values.items():
            row[self._columns[name]] = value
        return row

    def append(self, vacancies: Sequence[Vacancy]) -> None:
        rows = [self._layout(to_row(v)) for v in vacancies]
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
