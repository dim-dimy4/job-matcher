"""Status buttons (SPEC §8): a press under a Telegram message sets the status in the sheet.

No server waits for presses: the bot runs in GitHub Actions. Telegram keeps the presses
for 24 hours, and a short workflow picks them up every 15 minutes (getUpdates), so the
status lands in the sheet a few minutes after the press, not at once.

Presses are confirmed to Telegram only after the sheet is written. If the sheet fails,
they come back next run; setting the same status twice changes nothing, so a repeat is safe.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol

from job_matcher.notifier.telegram import TelegramBot, TelegramError
from job_matcher.tracker.sheets import BUTTON_STATUSES, StatusChange

log = logging.getLogger(__name__)


class StatusTracker(Protocol):
    def set_status(self, vacancy_id: str, status: str, today: date) -> StatusChange: ...


@dataclass(frozen=True)
class Press:
    message_id: int
    vacancy_id: str
    status: str


def parse_press(update: dict[str, Any], chat_id: str) -> Press | None:
    """A press of one of our buttons by the owner, or None for anything else."""
    query = update.get("callback_query") or {}
    message = query.get("message") or {}
    # A personal chat: its id is the owner's user id. Presses from anyone else are ignored.
    if str((query.get("from") or {}).get("id")) != chat_id:
        return None
    if str((message.get("chat") or {}).get("id")) != chat_id or "message_id" not in message:
        return None
    status, _, vacancy_id = str(query.get("data", "")).partition(":")
    if status not in BUTTON_STATUSES or not vacancy_id:
        return None
    return Press(message["message_id"], vacancy_id, status)


def process_presses(bot: TelegramBot, tracker: StatusTracker, *, today: date | None = None) -> int:
    """Apply pending presses to the sheet; returns how many statuses were set.

    Raises TelegramError if the presses can't be fetched or confirmed.
    """
    today = today or datetime.now(UTC).date()
    updates = bot.get_presses()
    if not updates:
        log.info("No button presses")
        return 0
    # Several presses on one message: only the last one counts.
    latest: dict[str, Press] = {}
    for update in updates:
        if press := parse_press(update, bot.chat_id):
            latest.pop(press.vacancy_id, None)
            latest[press.vacancy_id] = press

    applied = 0
    for press in latest.values():
        change = tracker.set_status(press.vacancy_id, press.status, today)
        if change is StatusChange.UNCHANGED:
            continue
        if change is not StatusChange.SET:
            log.warning("Button %s on vacancy %s: %s in the sheet", press.status,
                        press.vacancy_id, change)  # fmt: skip
            continue
        applied += 1
        try:
            bot.mark_chosen(press.message_id, press.vacancy_id, press.status)
        except TelegramError as exc:  # the sheet is what matters; the mark is cosmetic
            log.warning("Button %s on vacancy %s: mark not shown: %s", press.status,
                        press.vacancy_id, exc)  # fmt: skip

    bot.get_presses(offset=updates[-1]["update_id"] + 1)
    log.info("%d button presses, %d statuses set", len(latest), applied)
    return applied
