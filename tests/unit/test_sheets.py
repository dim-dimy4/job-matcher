"""The Google Sheet tracker against an in-memory worksheet; no Google API calls."""

from datetime import UTC, date, datetime

import pytest

from job_matcher.tracker import sheets
from job_matcher.tracker.sheets import (
    HEADER,
    KnownVacancies,
    SheetTracker,
    StatusChange,
    same_job_key,
    to_row,
)


class FakeWorksheet:
    def __init__(self, rows=None):
        self.rows = [list(r) for r in rows or []]
        self.input_options = []

    def get_all_values(self):
        return [list(r) for r in self.rows]

    def append_rows(self, values, value_input_option):
        # Like Sheets: new rows go right after the last non-empty row.
        while self.rows and not any(self.rows[-1]):
            self.rows.pop()
        self.rows.extend(list(r) for r in values)
        self.input_options.append(value_input_option)

    def batch_update(self, data, value_input_option):
        for cell in data:
            column, row = cell["range"][0], int(cell["range"][1:])  # "J5": single letters only
            line = self.rows[row - 1]
            line.extend([""] * (len(HEADER) - len(line)))
            line[ord(column) - ord("A")] = cell["values"][0][0]
        self.input_options.append(value_input_option)


def _sheet_row(company, title, url, status="New", applied_on=""):
    row = [""] * len(HEADER)
    row[sheets.COMPANY], row[sheets.VACANCY], row[sheets.LINK] = company, title, url
    row[sheets.STATUS], row[sheets.APPLIED_ON] = status, applied_on
    return row


def test_row_matches_the_header(make_vacancy):
    v = make_vacancy(
        source="himalayas",
        url="https://himalayas.app/companies/codest/jobs/senior-qa",
        title="Senior QA Automation Engineer",
        company="Codest",
        location="Poland",
        remote="yes",
        salary_min=18000,
        salary_max=23000,
        currency="PLN",
        contract_type="B2B",
        score=85,
        match_reasons=["+30 role: QA", "+20 seniority: senior"],
        collected_at=datetime(2026, 10, 31, 9, 0, tzinfo=UTC),
    )

    assert dict(zip(HEADER, to_row(v), strict=False)) == {
        "Date": "2026-10-31",
        "Source": "himalayas",
        "Company": "Codest",
        "Vacancy": "Senior QA Automation Engineer",
        "Link": "https://himalayas.app/companies/codest/jobs/senior-qa",
        "Location/format": "Poland · remote",
        "Salary range": "18000-23000 PLN, B2B",
        "Score": 85,
        "Reasons": "+30 role: QA; +20 seniority: senior",
        "Status": "New",
    }


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"salary_min": 15000, "salary_max": 20000, "currency": "PLN"}, "15000-20000 PLN"),
        ({"salary_min": 15000, "currency": "PLN"}, "from 15000 PLN"),
        ({"salary_max": 5000, "currency": "USD", "contract_type": "UoP"}, "up to 5000 USD, UoP"),
        ({}, ""),
    ],
    ids=["range", "only-min", "only-max", "none"],
)
def test_salary_range(make_vacancy, fields, expected):
    assert sheets.salary_range(make_vacancy(**fields)) == expected


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"location": "Wrocław", "remote": "hybrid"}, "Wrocław · hybrid"),
        ({"remote": "yes"}, "remote"),
        ({"location": "Kraków", "remote": "no"}, "Kraków · on-site"),
        ({}, ""),
    ],
)
def test_location_format(make_vacancy, fields, expected):
    assert sheets.location_format(make_vacancy(**fields)) == expected


def test_unscored_vacancy_has_an_empty_score_cell(make_vacancy):
    assert to_row(make_vacancy())[HEADER.index("Score")] == ""


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (("Ascendix Technologies", "QA Engineer (AI products)"),
         ("ascendix technologies", "QA Engineer - AI products")),
        (("Acme", "Senior  QA"), ("Acme", "Senior QA")),
    ],
    ids=["case-and-punctuation", "spaces"],
)  # fmt: skip
def test_same_job_key_ignores_formatting(first, second):
    assert same_job_key(*first) == same_job_key(*second)


def test_same_job_key_keeps_company_and_title_apart():
    assert same_job_key("QA", "Lead Engineer") != same_job_key("QA Lead", "Engineer")


def test_known_by_url_or_by_company_and_title(make_vacancy):
    known = KnownVacancies([_sheet_row("Acme", "Senior QA", "https://jobs.dou.ua/vacancies/1/")])

    assert make_vacancy(url="https://jobs.dou.ua/vacancies/1/", title="Renamed") in known
    assert (
        make_vacancy(url="https://himalayas.app/jobs/x", company="ACME", title="Senior QA") in known
    )
    assert make_vacancy(url="https://himalayas.app/jobs/y", title="Lead QA") not in known


def test_short_rows_are_ignored(make_vacancy):
    known = KnownVacancies([["2026-10-31", "dou", "Acme"], []])

    assert make_vacancy(company="Acme") not in known


def test_tracker_reads_known_vacancies_without_the_header(make_vacancy):
    worksheet = FakeWorksheet([HEADER, _sheet_row("Acme", "Senior QA", "https://a.example/1")])

    tracker = SheetTracker(worksheet)

    assert make_vacancy(url="https://a.example/1") in tracker.known
    assert make_vacancy(company="Company", title="Vacancy", url="https://a.example/2") not in (
        tracker.known
    ), "the header row is not a vacancy"


@pytest.mark.parametrize("rows", [[], [[]]], ids=["no-rows", "one-empty-row"])
def test_empty_sheet_gets_the_header_first(make_vacancy, rows):
    worksheet = FakeWorksheet(rows)
    v = make_vacancy(score=80)

    tracker = SheetTracker(worksheet)
    tracker.append([v])
    tracker.append([make_vacancy(url="https://a.example/2", title="Lead QA", score=75)])

    assert worksheet.rows[0] == HEADER
    assert len(worksheet.rows) == 3, "the header is written once"
    assert v in tracker.known, "appended vacancies become known"


def test_rows_are_written_raw(make_vacancy):
    worksheet = FakeWorksheet([HEADER])

    SheetTracker(worksheet).append([make_vacancy(title='=HYPERLINK("http://evil")', score=80)])

    assert worksheet.input_options == ["RAW"], "USER_ENTERED would run the title as a formula"


def test_nothing_to_append_makes_no_api_call():
    worksheet = FakeWorksheet([HEADER])

    SheetTracker(worksheet).append([])

    assert worksheet.input_options == []


def test_open_worksheet_asks_only_for_the_sheets_scope(monkeypatch):
    calls = {}

    class Client:
        def open_by_key(self, key):
            calls["key"] = key
            return type("Spreadsheet", (), {"sheet1": "first worksheet"})()

    def service_account_from_dict(info, scopes):
        calls["info"], calls["scopes"] = info, scopes
        return Client()

    monkeypatch.setattr(sheets.gspread, "service_account_from_dict", service_account_from_dict)

    assert sheets.open_worksheet({"type": "service_account"}, "sheet-id") == "first worksheet"
    assert calls == {
        "info": {"type": "service_account"},
        "scopes": ["https://www.googleapis.com/auth/spreadsheets"],
        "key": "sheet-id",
    }


# ---------- statuses from the Telegram buttons ----------

URL = "https://a.example/1"
TODAY = date(2026, 11, 1)


def _status_sheet(status="New", applied_on=""):
    other = _sheet_row("Beta", "QA Lead", "https://a.example/2")
    return FakeWorksheet([HEADER, other, _sheet_row("Acme", "Senior QA", URL, status, applied_on)])


def _cells(worksheet, n=3):
    row = worksheet.rows[n - 1]
    return row[sheets.STATUS], row[sheets.APPLIED_ON]


def test_applied_sets_status_and_date_raw(make_vacancy):
    worksheet = _status_sheet()

    change = SheetTracker(worksheet).set_status(make_vacancy(url=URL).id, "Applied", TODAY)

    assert change is StatusChange.SET
    assert _cells(worksheet) == ("Applied", "2026-11-01")
    assert _cells(worksheet, 2) == ("New", ""), "only the pressed vacancy's row changes"
    assert worksheet.input_options == ["RAW"]


def test_applied_keeps_a_date_already_there(make_vacancy):
    worksheet = _status_sheet(status="Skipped", applied_on="2026-10-20")

    SheetTracker(worksheet).set_status(make_vacancy(url=URL).id, "Applied", TODAY)

    assert _cells(worksheet) == ("Applied", "2026-10-20")


def test_skipped_takes_back_a_mistaken_applied(make_vacancy):
    worksheet = _status_sheet()
    tracker = SheetTracker(worksheet)
    vacancy_id = make_vacancy(url=URL).id

    tracker.set_status(vacancy_id, "Applied", TODAY)
    change = tracker.set_status(vacancy_id, "Skipped", TODAY)

    assert change is StatusChange.SET, "the tracker sees its own first change"
    assert _cells(worksheet) == ("Skipped", "")


@pytest.mark.parametrize("status", ["HR", "Technical", "Offer", "Rejected"])
def test_a_status_set_by_hand_is_kept(make_vacancy, status):
    worksheet = _status_sheet(status=status)

    change = SheetTracker(worksheet).set_status(make_vacancy(url=URL).id, "Applied", TODAY)

    assert change is StatusChange.KEPT
    assert worksheet.input_options == []


def test_the_same_status_again_writes_nothing(make_vacancy):
    worksheet = _status_sheet(status="Skipped")

    change = SheetTracker(worksheet).set_status(make_vacancy(url=URL).id, "Skipped", TODAY)

    assert change is StatusChange.UNCHANGED
    assert worksheet.input_options == []


def test_a_row_without_trailing_cells_is_updated(make_vacancy):
    short = _sheet_row("Acme", "Senior QA", URL, status="")[: sheets.LINK + 1]
    worksheet = FakeWorksheet([HEADER, short])

    change = SheetTracker(worksheet).set_status(make_vacancy(url=URL).id, "Applied", TODAY)

    assert change is StatusChange.SET
    assert _cells(worksheet, 2) == ("Applied", "2026-11-01")


def test_unknown_vacancy_is_not_found():
    worksheet = _status_sheet()

    assert SheetTracker(worksheet).set_status("ffffffffffffffff", "Applied", TODAY) is (
        StatusChange.NOT_FOUND
    )
    assert worksheet.input_options == []
