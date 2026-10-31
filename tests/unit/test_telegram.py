"""Telegram notifier with the Bot API mocked by respx; nothing is sent for real."""

import json

import httpx
import pytest
import respx

from job_matcher.notifier.telegram import TelegramError, TelegramNotifier, format_message

TOKEN = "123:secret-token"
SEND_URL = f"https://api.telegram.org/bot{TOKEN}/sendMessage"


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
def notifier():
    with httpx.Client() as client:
        yield TelegramNotifier(TOKEN, "42", client)


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
def test_send_posts_html_without_link_preview(notifier, scored):
    route = respx.post(SEND_URL).mock(return_value=httpx.Response(200, json={"ok": True}))

    notifier.send(scored())

    payload = json.loads(route.calls.last.request.content)
    assert payload["chat_id"] == "42"
    assert payload["parse_mode"] == "HTML"
    assert payload["link_preview_options"] == {"is_disabled": True}
    assert payload["text"].startswith("<b>Senior QA Automation Engineer</b>")


@respx.mock
@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(400, json={"ok": False}), "Telegram returned HTTP 400"),
        (httpx.Response(429, json={"ok": False}), "Telegram returned HTTP 429"),
        (httpx.ConnectTimeout("timed out"), "Telegram request failed: ConnectTimeout"),
    ],
    ids=["bad-request", "rate-limited", "timeout"],
)
def test_errors_never_contain_the_token(notifier, scored, response, message):
    respx.post(SEND_URL).mock(side_effect=[response])

    with pytest.raises(TelegramError, match=message) as error:
        notifier.send(scored())

    assert TOKEN not in str(error.value)
    assert error.value.__cause__ is None, "httpx errors quote the request URL with the token"
