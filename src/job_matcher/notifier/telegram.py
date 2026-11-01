"""Telegram notifications (SPEC §8): one message per vacancy from a personal bot, with
status buttons under it."""

from __future__ import annotations

import html
from typing import Any

import httpx

from job_matcher.models import Vacancy
from job_matcher.tracker.sheets import BUTTON_STATUSES, location_format, salary_range

API_URL = "https://api.telegram.org/bot{token}/{method}"
MAX_REASONS = 3
CHOSEN_MARK = "✓ "


class TelegramError(RuntimeError):
    pass


def format_message(v: Vacancy) -> str:
    """HTML message; every value from a job board is escaped, so a "<" in a title can't
    break the markup (Telegram rejects the whole message on invalid HTML)."""
    # The role reason is true for every notified vacancy, so it carries no information.
    reasons = [r for r in v.match_reasons if " role: " not in r][:MAX_REASONS]
    lines = [
        f"<b>{html.escape(v.title)}</b>",
        html.escape(v.company),
        html.escape(location_format(v)),
        html.escape(salary_range(v)),
        f"Score: <b>{v.score}</b>",
        *(f"• {html.escape(r)}" for r in reasons),
        f'<a href="{html.escape(str(v.url))}">Open the vacancy</a>',
    ]
    return "\n".join(line for line in lines if line)


def keyboard(vacancy_id: str, chosen: str | None = None) -> dict[str, Any]:
    """One row of status buttons; the chosen one is marked. Button data is
    "<status>:<vacancy id>", well under Telegram's 64-byte limit."""
    buttons = [
        {"text": f"{CHOSEN_MARK}{s}" if s == chosen else s, "callback_data": f"{s}:{vacancy_id}"}
        for s in BUTTON_STATUSES
    ]
    return {"inline_keyboard": [buttons]}


class TelegramBot:
    def __init__(self, token: str, chat_id: str, client: httpx.Client) -> None:
        self._token = token
        self.chat_id = chat_id
        self._client = client

    def _call(self, method: str, payload: dict[str, Any]) -> Any:
        try:
            response = self._client.post(
                API_URL.format(token=self._token, method=method), json=payload
            )
            response.raise_for_status()
            return response.json()["result"]
        except httpx.HTTPStatusError as exc:
            # Only the status: the request URL contains the bot token, and CI logs are public.
            raise TelegramError(
                f"Telegram {method} returned HTTP {exc.response.status_code}"
            ) from None
        except httpx.HTTPError as exc:
            raise TelegramError(f"Telegram {method} failed: {type(exc).__name__}") from None
        except (ValueError, KeyError, TypeError):
            raise TelegramError(f"Telegram {method} returned an unexpected response") from None

    def send(self, v: Vacancy) -> None:
        self._call(
            "sendMessage",
            {
                "chat_id": self.chat_id,
                "text": format_message(v),
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
                "reply_markup": keyboard(v.id),
            },
        )

    def get_presses(self, offset: int | None = None) -> list[dict[str, Any]]:
        """Button presses Telegram keeps for us (up to 24 hours). Calling with `offset`
        confirms every update before it, so it is not returned again."""
        payload: dict[str, Any] = {"timeout": 0, "allowed_updates": ["callback_query"]}
        if offset is not None:
            payload |= {"offset": offset, "limit": 1}
        return self._call("getUpdates", payload)

    def mark_chosen(self, message_id: int, vacancy_id: str, status: str) -> None:
        self._call(
            "editMessageReplyMarkup",
            {"chat_id": self.chat_id, "message_id": message_id,
             "reply_markup": keyboard(vacancy_id, status)},
        )  # fmt: skip
