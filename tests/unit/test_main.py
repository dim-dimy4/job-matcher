"""The pipeline with fake outputs: what goes to the sheet, what goes to Telegram, in which order."""

import logging

import pytest

from job_matcher import main as pipeline
from job_matcher.notifier.telegram import TelegramError
from job_matcher.profile import Profile
from job_matcher.settings import Settings, SettingsError
from job_matcher.tracker.sheets import KnownVacancies


@pytest.fixture
def profile():
    return Profile(
        role_keywords=["QA"],
        remote_locations=["Poland"],
        plus_keywords=["Playwright", "Python"],
        notify_threshold=65,
    )


@pytest.fixture
def job(make_vacancy):
    """A remote senior QA job: 60 points, plus 5 per keyword in `skills`."""

    def _make(n, skills="", **overrides):
        data = {
            "url": f"https://jobs.dou.ua/vacancies/{n}/",
            "title": f"Senior QA Engineer {n}",
            "remote": "yes",
            "location": "Poland",
            "seniority": "senior",
            "description": skills,
        }
        return make_vacancy(**{**data, **overrides})

    return _make


class FakeTracker:
    def __init__(self, events, known=()):
        self.events = events
        self.known = KnownVacancies()
        for v in known:
            self.known.add(v)
        self.appended = []

    def append(self, vacancies):
        self.events.append("sheet")
        self.appended.extend(vacancies)


class FakeNotifier:
    def __init__(self, events, fail_on=()):
        self.events = events
        self.fail_on = set(fail_on)
        self.sent = []

    def send(self, v):
        self.events.append("telegram")
        if v.title in self.fail_on:
            raise TelegramError("Telegram returned HTTP 400")
        self.sent.append(v)


@pytest.fixture
def events():
    return []


@pytest.fixture
def tracker(events):
    return FakeTracker(events)


@pytest.fixture
def notifier(events):
    return FakeNotifier(events)


@pytest.fixture
def no_sleep(monkeypatch):
    delays = []
    monkeypatch.setattr(pipeline.time, "sleep", delays.append)
    return delays


def _titles(vacancies):
    return [v.title for v in vacancies]


def test_select_new_keeps_matches_best_first(profile, job):
    vacancies = [job(1), job(2, "Python"), job(3, "Playwright Python", title="Junior QA"),
                 job(4, "Playwright Python")]  # fmt: skip

    selected = pipeline.select_new(vacancies, profile, KnownVacancies())

    assert [(v.title, v.score) for v in selected] == [
        ("Senior QA Engineer 4", 70),
        ("Senior QA Engineer 2", 65),
    ], "60 is below the threshold, the junior job is rejected"


def test_select_new_skips_known_and_cross_board_duplicates(profile, job):
    known = KnownVacancies()
    known.add(job(1, "Python"))
    vacancies = [
        job(1, "Python"),  # already in the sheet
        job(2, "Python", source="himalayas", url="https://himalayas.app/jobs/qa-2"),
        job(2, "Playwright Python"),  # the same job on DOU, scored higher
    ]

    selected = pipeline.select_new(vacancies, profile, known)

    assert [(str(v.url), v.score) for v in selected] == [("https://jobs.dou.ua/vacancies/2/", 70)]


def test_run_writes_the_sheet_before_telegram(profile, job, events, tracker, notifier, no_sleep):
    failures = pipeline.run([job(1, "Python"), job(2, "Python")], profile, tracker, notifier)

    assert failures == 0
    assert events == ["sheet", "telegram", "telegram"]
    assert _titles(notifier.sent) == _titles(tracker.appended)


def test_run_limits_messages_but_logs_all(profile, job, tracker, notifier, no_sleep):
    vacancies = [job(n, "Python") for n in range(5)]

    pipeline.run(vacancies, profile, tracker, notifier, limit=2, delay=0.5)

    assert len(tracker.appended) == 5
    assert len(notifier.sent) == 2
    assert no_sleep == [0.5], "a pause between messages, not before the first"


def test_run_continues_after_a_failed_message(profile, job, tracker, notifier, no_sleep, caplog):
    notifier.fail_on = {"Senior QA Engineer 1"}

    with caplog.at_level(logging.INFO, logger="job_matcher"):
        failures = pipeline.run([job(1, "Python"), job(2, "Python")], profile, tracker, notifier)

    assert failures == 1
    assert _titles(notifier.sent) == ["Senior QA Engineer 2"]
    assert "Senior QA Engineer 1 @ Acme not sent: Telegram returned HTTP 400" in caplog.text
    assert "2 collected, 2 new matches, 1 sent to Telegram, 1 failed" in caplog.text


def test_run_with_nothing_new_sends_nothing(profile, job, events, notifier, no_sleep):
    tracker = FakeTracker(events, known=[job(1, "Python")])

    assert pipeline.run([job(1, "Python")], profile, tracker, notifier) == 0
    assert events == ["sheet"]
    assert tracker.appended == []


def test_collect_all_merges_both_boards(monkeypatch, job):
    monkeypatch.setattr(pipeline.dou, "collect", lambda client: [job(1)])
    monkeypatch.setattr(pipeline.himalayas, "collect", lambda client: [job(2)])

    assert _titles(pipeline.collect_all(client=None)) == ["Senior QA Engineer 1",
                                                          "Senior QA Engineer 2"]  # fmt: skip


# ---------- main(): the wiring, with every external call replaced ----------


@pytest.fixture
def wired(monkeypatch, profile, job, events):
    """main() with no .env, no network, no Google and no Telegram."""
    tracker = FakeTracker(events)
    notifier = FakeNotifier(events)
    calls = {"collected": 0}

    def collect_all(client):
        calls["collected"] += 1
        return [job(1, "Python"), job(2)]

    monkeypatch.setattr(pipeline, "load_dotenv", lambda: None)
    monkeypatch.setattr(pipeline, "load_profile", lambda: profile)
    monkeypatch.setattr(pipeline, "collect_all", collect_all)
    monkeypatch.setattr(pipeline, "load_settings", lambda: SETTINGS)
    monkeypatch.setattr(pipeline, "open_worksheet", lambda info, sheet_id: "worksheet")
    monkeypatch.setattr(pipeline, "SheetTracker", lambda worksheet: tracker)
    monkeypatch.setattr(pipeline, "TelegramNotifier", lambda token, chat_id, client: notifier)
    return calls, tracker, notifier


SETTINGS = Settings(
    telegram_token="123:secret-token",
    telegram_chat_id="42",
    service_account={"type": "service_account"},
    sheet_id="sheet-id",
)


def test_main_full_run(wired):
    _, tracker, notifier = wired

    assert pipeline.main([]) == 0
    assert _titles(tracker.appended) == ["Senior QA Engineer 1"]
    assert _titles(notifier.sent) == ["Senior QA Engineer 1"]


def test_main_fails_when_a_message_fails(wired):
    _, _, notifier = wired
    notifier.fail_on = {"Senior QA Engineer 1"}

    assert pipeline.main([]) == 1


def test_main_stops_before_collecting_without_secrets(wired, monkeypatch, caplog):
    calls, _, _ = wired

    def missing():
        raise SettingsError("missing environment variables: SHEET_ID")

    monkeypatch.setattr(pipeline, "load_settings", missing)

    assert pipeline.main([]) == 2
    assert calls["collected"] == 0
    assert "missing environment variables: SHEET_ID" in caplog.text


def test_main_dry_run_prints_and_touches_no_outputs(wired, monkeypatch, capsys):
    _, tracker, notifier = wired
    monkeypatch.setattr(pipeline, "load_settings", lambda: pytest.fail("secrets not needed"))

    assert pipeline.main(["--dry-run"]) == 0

    out = capsys.readouterr().out
    assert " 65  Senior QA Engineer 1 @ Acme  | Poland · remote" in out
    assert "1 of 2 vacancies reach 65" in out
    assert (tracker.appended, notifier.sent) == ([], [])


def test_main_keeps_the_httpx_logger_quiet(wired):
    pipeline.main([])

    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING, (
        "httpx logs request URLs at INFO, and the Telegram URL contains the bot token"
    )
