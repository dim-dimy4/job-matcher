"""Telegram notifications (SPEC §8): one message per vacancy from a personal bot."""

from __future__ import annotations

import html

import httpx

from job_matcher.models import Vacancy
from job_matcher.tracker.sheets import location_format, salary_range

API_URL = "https://api.telegram.org/bot{token}/sendMessage"
MAX_REASONS = 3


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


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, client: httpx.Client) -> None:
        self._url = API_URL.format(token=token)
        self._chat_id = chat_id
        self._client = client

    def send(self, v: Vacancy) -> None:
        payload = {
            "chat_id": self._chat_id,
            "text": format_message(v),
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        try:
            response = self._client.post(self._url, json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # Only the status: the request URL contains the bot token, and CI logs are public.
            raise TelegramError(f"Telegram returned HTTP {exc.response.status_code}") from None
        except httpx.HTTPError as exc:
            raise TelegramError(f"Telegram request failed: {type(exc).__name__}") from None
