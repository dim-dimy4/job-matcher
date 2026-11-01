"""Button presses → sheet statuses, with a fake bot and a fake sheet."""

import logging
from datetime import UTC, date, datetime

import pytest

from job_matcher.notifier import buttons
from job_matcher.notifier.buttons import Press, parse_press, process_presses
from job_matcher.notifier.telegram import TelegramError
from job_matcher.tracker.sheets import StatusChange

OWNER = "42"
TODAY = date(2026, 11, 1)


def _update(update_id, data, *, user=42, chat=42, message_id=100):
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"q{update_id}",
            "from": {"id": user},
            "message": {"message_id": message_id, "chat": {"id": chat}},
            "data": data,
        },
    }


class FakeBot:
    chat_id = OWNER

    def __init__(self, updates=(), fail_mark=False):
        self.updates = list(updates)
        self.fail_mark = fail_mark
        self.marked = []
        self.confirmed = []

    def get_presses(self, offset=None):
        if offset is None:
            return self.updates
        self.confirmed.append(offset)
        return []

    def mark_chosen(self, message_id, vacancy_id, status):
        if self.fail_mark:
            raise TelegramError("Telegram editMessageReplyMarkup returned HTTP 400")
        self.marked.append((message_id, vacancy_id, status))


class FakeTracker:
    def __init__(self, answer=StatusChange.SET, fail=False):
        self.answer = answer
        self.fail = fail
        self.calls = []

    def set_status(self, vacancy_id, status, today):
        if self.fail:
            raise RuntimeError("Sheets API error")
        self.calls.append((vacancy_id, status, today))
        return self.answer


def test_press_by_the_owner():
    assert parse_press(_update(1, "Applied:abc"), OWNER) == Press(100, "abc", "Applied")


@pytest.mark.parametrize(
    "update",
    [
        _update(1, "Applied:abc", user=7),
        _update(1, "Applied:abc", chat=7),
        _update(1, "Offer:abc"),
        _update(1, "Applied:"),
        _update(1, "garbage"),
        {"update_id": 1, "message": {"text": "hi"}},
        {"update_id": 1, "callback_query": {"from": {"id": 42}, "data": "Applied:abc"}},
    ],
    ids=["other-user", "other-chat", "not-a-button-status", "no-vacancy", "no-separator",
         "not-a-press", "no-message"],
)  # fmt: skip
def test_anything_else_is_ignored(update):
    assert parse_press(update, OWNER) is None


def test_presses_set_statuses_mark_buttons_and_are_confirmed():
    bot = FakeBot([_update(5, "Applied:abc", message_id=100),
                   _update(6, "Skipped:def", message_id=101)])  # fmt: skip
    tracker = FakeTracker()

    assert process_presses(bot, tracker, today=TODAY) == 2
    assert tracker.calls == [("abc", "Applied", TODAY), ("def", "Skipped", TODAY)]
    assert bot.marked == [(100, "abc", "Applied"), (101, "def", "Skipped")]
    assert bot.confirmed == [7], "offset = last update_id + 1"


def test_only_the_last_press_on_a_vacancy_counts():
    bot = FakeBot([_update(1, "Applied:abc"), _update(2, "Skipped:def"),
                   _update(3, "Skipped:abc")])  # fmt: skip
    tracker = FakeTracker()

    process_presses(bot, tracker, today=TODAY)

    assert tracker.calls == [("def", "Skipped", TODAY), ("abc", "Skipped", TODAY)]


def test_foreign_presses_are_confirmed_but_not_applied():
    bot = FakeBot([_update(9, "Applied:abc", user=7)])
    tracker = FakeTracker()

    assert process_presses(bot, tracker, today=TODAY) == 0
    assert tracker.calls == []
    assert bot.confirmed == [10], "otherwise they would come back every run"


def test_no_presses_makes_no_confirm_call():
    bot = FakeBot()

    assert process_presses(bot, FakeTracker(), today=TODAY) == 0
    assert bot.confirmed == []


@pytest.mark.parametrize(
    ("answer", "logged"),
    [(StatusChange.KEPT, "Button Applied on vacancy abc: kept in the sheet"),
     (StatusChange.NOT_FOUND, "Button Applied on vacancy abc: not found in the sheet")],
    ids=["status-set-by-hand", "row-deleted"],
)  # fmt: skip
def test_a_press_the_sheet_refuses_leaves_the_buttons(answer, logged, caplog):
    bot = FakeBot([_update(1, "Applied:abc")])

    assert process_presses(bot, FakeTracker(answer), today=TODAY) == 0
    assert bot.marked == []
    assert bot.confirmed == [2]
    assert logged in caplog.text


def test_the_same_press_again_is_quiet(caplog):
    bot = FakeBot([_update(1, "Applied:abc")])

    with caplog.at_level(logging.WARNING):
        assert process_presses(bot, FakeTracker(StatusChange.UNCHANGED), today=TODAY) == 0

    assert bot.marked == []
    assert caplog.text == ""


def test_a_failed_mark_still_counts_the_status(caplog):
    bot = FakeBot([_update(1, "Applied:abc")], fail_mark=True)

    assert process_presses(bot, FakeTracker(), today=TODAY) == 1
    assert bot.confirmed == [2]
    assert "mark not shown: Telegram editMessageReplyMarkup returned HTTP 400" in caplog.text


def test_a_sheet_failure_leaves_the_presses_in_telegram():
    bot = FakeBot([_update(1, "Applied:abc")])

    with pytest.raises(RuntimeError):
        process_presses(bot, FakeTracker(fail=True), today=TODAY)

    assert bot.confirmed == [], "unconfirmed presses come back next run"


def test_today_defaults_to_the_utc_date(monkeypatch):
    class Clock:
        @staticmethod
        def now(tz):
            assert tz is UTC
            return datetime(2026, 11, 2, 0, 30, tzinfo=UTC)

    monkeypatch.setattr(buttons, "datetime", Clock)
    tracker = FakeTracker()

    process_presses(FakeBot([_update(1, "Applied:abc")]), tracker)

    assert tracker.calls[0][2] == date(2026, 11, 2)
