"""Telegram bot with the Bot API mocked by respx; nothing is sent for real."""

import json

import httpx
import pytest
import respx

from job_matcher.notifier.telegram import TelegramBot, TelegramError, format_message, keyboard

TOKEN = "123:secret-token"
API = f"https://api.telegram.org/bot{TOKEN}"
SEND_URL = f"{API}/sendMessage"
VACANCY_ID = "0123456789abcdef"


@pytest.fixture
def scored(make_vacancy):
    def _make(**overrides):
        data = {
            "location": "Poland",
            "remote": "yes",
            "salary_min": 18000,
            "salary_max": 23000,
            "currency": "PLN",
            "contract_type": "B2B",
            "score": 85,
            "match_reasons": [
                "+30 role: QA",
                "+20 seniority: senior",
                "+15 keywords: Playwright, Python, API",
                "+10 remote",
                "+10 salary up to 23000 PLN, B2B reaches the target",
            ],
        }
        return make_vacancy(**{**data, **overrides})

    return _make


@pytest.fixture
def bot():
    with httpx.Client() as client:
        yield TelegramBot(TOKEN, "42", client)


def _ok(result=True):
    return httpx.Response(200, json={"ok": True, "result": result})


def test_message_layout(scored):
    assert format_message(scored()) == "\n".join(
        [
            "<b>Senior QA Automation Engineer</b>",
            "Acme",
            "Poland · remote",
            "18000-23000 PLN, B2B",
            "Score: <b>85</b>",
            "• +20 seniority: senior",
            "• +15 keywords: Playwright, Python, API",
            "• +10 remote",
            '<a href="https://jobs.dou.ua/companies/acme/vacancies/123/">Open the vacancy</a>',
        ]
    )


def test_empty_fields_leave_no_blank_lines(make_vacancy):
    lines = format_message(make_vacancy(score=70)).splitlines()

    assert "" not in lines
    assert len(lines) == 4  # title, company, score, link


def test_values_from_the_job_board_are_escaped(scored):
    message = format_message(scored(title="QA <Lead> & Mentor", company="R&D <script>"))

    assert "<b>QA &lt;Lead&gt; &amp; Mentor</b>" in message
    assert "R&amp;D &lt;script&gt;" in message


@respx.mock
def test_send_posts_html_without_link_preview(bot, scored):
    route = respx.post(SEND_URL).mock(return_value=_ok())

    bot.send(scored())

    payload = json.loads(route.calls.last.request.content)
    assert payload["chat_id"] == "42"
    assert payload["parse_mode"] == "HTML"
    assert payload["link_preview_options"] == {"is_disabled": True}
    assert payload["text"].startswith("<b>Senior QA Automation Engineer</b>")
    assert payload["reply_markup"] == keyboard(scored().id)


def test_keyboard_marks_the_chosen_status():
    assert keyboard(VACANCY_ID, "Applied") == {
        "inline_keyboard": [[
            {"text": "✓ Applied", "callback_data": f"Applied:{VACANCY_ID}"},
            {"text": "Skipped", "callback_data": f"Skipped:{VACANCY_ID}"},
        ]]
    }  # fmt: skip


def test_button_data_fits_telegram_limit(scored):
    for button in keyboard(scored().id)["inline_keyboard"][0]:
        assert len(button["callback_data"].encode()) <= 64


@respx.mock
def test_get_presses_asks_only_for_button_presses(bot):
    presses = [{"update_id": 7, "callback_query": {}}]
    route = respx.post(f"{API}/getUpdates").mock(return_value=_ok(presses))

    assert bot.get_presses() == presses
    assert json.loads(route.calls.last.request.content) == {
        "timeout": 0,
        "allowed_updates": ["callback_query"],
    }


@respx.mock
def test_get_presses_with_offset_confirms_the_earlier_ones(bot):
    route = respx.post(f"{API}/getUpdates").mock(return_value=_ok([]))

    bot.get_presses(offset=8)

    payload = json.loads(route.calls.last.request.content)
    assert (payload["offset"], payload["limit"]) == (8, 1)


@respx.mock
def test_mark_chosen_edits_only_the_buttons(bot):
    route = respx.post(f"{API}/editMessageReplyMarkup").mock(return_value=_ok())

    bot.mark_chosen(99, VACANCY_ID, "Skipped")

    assert json.loads(route.calls.last.request.content) == {
        "chat_id": "42",
        "message_id": 99,
        "reply_markup": keyboard(VACANCY_ID, "Skipped"),
    }


@respx.mock
@pytest.mark.parametrize(
    "response",
    [httpx.Response(200, text="<html>"), httpx.Response(200, json={"ok": True})],
    ids=["not-json", "no-result"],
)
def test_unexpected_response_is_an_error(bot, response):
    respx.post(f"{API}/getUpdates").mock(return_value=response)

    with pytest.raises(TelegramError, match="getUpdates returned an unexpected response"):
        bot.get_presses()


@respx.mock
@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(400, json={"ok": False}), "Telegram sendMessage returned HTTP 400"),
        (httpx.Response(429, json={"ok": False}), "Telegram sendMessage returned HTTP 429"),
        (httpx.ConnectTimeout("timed out"), "Telegram sendMessage failed: ConnectTimeout"),
    ],
    ids=["bad-request", "rate-limited", "timeout"],
)
def test_errors_never_contain_the_token(bot, scored, response, message):
    respx.post(SEND_URL).mock(side_effect=[response])

    with pytest.raises(TelegramError, match=message) as error:
        bot.send(scored())

    assert TOKEN not in str(error.value)
    assert error.value.__cause__ is None, "httpx errors quote the request URL with the token"
