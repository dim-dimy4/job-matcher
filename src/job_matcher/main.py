"""One run of the pipeline (SPEC §4): button presses → collect → score → drop seen →
sheet → Telegram.

python -m job_matcher.main                 # full run, needs the secrets from .env
python -m job_matcher.main --dry-run       # collect and score only, print the matches
python -m job_matcher.main --buttons-only  # only apply the status buttons pressed
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Sequence
from typing import Protocol

import httpx
from dotenv import load_dotenv

from job_matcher.collectors import djinni, dou, himalayas
from job_matcher.collectors.http import make_client
from job_matcher.matcher import apply
from job_matcher.models import Vacancy
from job_matcher.notifier.buttons import process_presses
from job_matcher.notifier.telegram import TelegramBot, TelegramError
from job_matcher.profile import Profile, load_profile
from job_matcher.settings import SettingsError, load_settings
from job_matcher.tracker.sheets import (
    KnownVacancies,
    SheetTracker,
    location_format,
    open_worksheet,
    salary_range,
)

# SPEC §8, no spam: the best N go to Telegram, the rest only to the sheet.
MAX_MESSAGES_PER_RUN = 10
# Telegram allows about one message per second in a chat.
DELAY_BETWEEN_MESSAGES_SECONDS = 1.0

log = logging.getLogger("job_matcher")


class Tracker(Protocol):
    known: KnownVacancies

    def append(self, vacancies: Sequence[Vacancy]) -> None: ...


class Notifier(Protocol):
    def send(self, v: Vacancy) -> None: ...


def collect_all(client: httpx.Client) -> list[Vacancy]:
    # Each collector logs and skips its own failures, so one broken board doesn't stop the run.
    return [*dou.collect(client), *himalayas.collect(client), *djinni.collect(client)]


def select_new(
    vacancies: Sequence[Vacancy], profile: Profile, known: KnownVacancies
) -> list[Vacancy]:
    """Matches at or above the threshold that aren't known yet, best first.

    Selected vacancies are added to `known`, so a posting found on both boards in one run
    is kept once (the higher-scored copy, thanks to the sort).
    """
    scored = sorted(
        (apply(v, profile) for v in vacancies), key=lambda v: v.score or 0, reverse=True
    )
    new = []
    for v in scored:
        if (v.score or 0) >= profile.notify_threshold and v not in known:
            known.add(v)
            new.append(v)
    return new


def run(
    vacancies: Sequence[Vacancy],
    profile: Profile,
    tracker: Tracker,
    notifier: Notifier,
    *,
    limit: int = MAX_MESSAGES_PER_RUN,
    delay: float = DELAY_BETWEEN_MESSAGES_SECONDS,
) -> int:
    """Returns the number of failed notifications."""
    new = select_new(vacancies, profile, tracker.known)
    # The sheet first: if Telegram fails afterwards, the vacancy is still logged and isn't
    # sent again next run. A missed message is better than the same message every 3 hours.
    tracker.append(new)
    failures = 0
    for i, v in enumerate(new[:limit]):
        if i:
            time.sleep(delay)
        try:
            notifier.send(v)
        except TelegramError as exc:
            failures += 1
            log.error("Telegram: %s @ %s not sent: %s", v.title, v.company, exc)
    log.info(
        "%d collected, %d new matches, %d sent to Telegram, %d failed",
        len(vacancies), len(new), min(len(new), limit) - failures, failures,
    )  # fmt: skip
    return failures


def _print_matches(vacancies: Sequence[Vacancy], profile: Profile) -> None:
    matches = select_new(vacancies, profile, KnownVacancies())
    # A Windows console defaults to a legacy code page without "ó" or Cyrillic.
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    for v in matches:
        print(f"{v.score:>3}  {v.title} @ {v.company}  | {location_format(v)}  {salary_range(v)}")
    print(f"\n{len(matches)} of {len(vacancies)} vacancies reach {profile.notify_threshold}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="job-matcher", description=__doc__.splitlines()[0])
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true", help="no sheet, no Telegram: print")
    modes.add_argument("--buttons-only", action="store_true", help="only apply button presses")
    args = parser.parse_args(argv)

    load_dotenv()  # local runs; in CI the variables come from GitHub Secrets
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    # httpx logs every request URL at INFO, and the Telegram URL contains the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    with make_client() as http:
        if args.dry_run:
            profile = load_profile()
            _print_matches(collect_all(http), profile)
            return 0
        try:  # before collecting: a missing secret fails fast, without hitting the job boards
            settings = load_settings()
        except SettingsError as exc:
            log.error("%s", exc)
            return 2
        tracker = SheetTracker(open_worksheet(settings.service_account, settings.sheet_id))
        bot = TelegramBot(settings.telegram_token, settings.telegram_chat_id, http)
        # Presses first, while the sheet is as it was read. A failure here is logged and
        # doesn't stop the search: the presses wait in Telegram for the next run.
        try:
            process_presses(bot, tracker)
            failures = 0
        except TelegramError as exc:
            log.error("Buttons: %s", exc)
            failures = 1
        if not args.buttons_only:
            failures += run(collect_all(http), load_profile(), tracker, bot)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
