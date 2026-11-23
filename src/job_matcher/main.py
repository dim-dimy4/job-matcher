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

from job_matcher.collectors import arbeitnow, ats, djinni, dou, himalayas, jobicy
from job_matcher.collectors.http import make_client
from job_matcher.funnel import Funnel, write_summary
from job_matcher.matcher import apply, rejection_rule
from job_matcher.models import Vacancy
from job_matcher.notifier.buttons import process_presses
from job_matcher.notifier.telegram import TelegramBot, TelegramError
from job_matcher.profile import Profile, load_profile
from job_matcher.settings import SettingsError, load_settings
from job_matcher.tracker.sheets import (
    KnownVacancies,
    SheetError,
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
    return [
        *dou.collect(client),
        *himalayas.collect(client),
        *djinni.collect(client),
        *jobicy.collect(client),
        *arbeitnow.collect(client),
        *ats.collect(client),
    ]


def select_new(
    vacancies: Sequence[Vacancy],
    profile: Profile,
    known: KnownVacancies,
    funnel: Funnel | None = None,
) -> list[Vacancy]:
    """Matches at or above the sheet threshold that aren't known yet, best first.

    Selected vacancies are added to `known`, so a posting found on both boards in one run
    is kept once (the higher-scored copy, thanks to the sort).
    """
    funnel = funnel if funnel is not None else Funnel()
    scored = sorted(
        (apply(v, profile) for v in vacancies), key=lambda v: v.score or 0, reverse=True
    )
    new = []
    for v in scored:
        if v.score == 0 and any(rejection_rule(r) for r in v.match_reasons):
            funnel.count_rejected(v)
        elif (v.score or 0) < profile.sheet_threshold:
            funnel.below_threshold += 1
            funnel.note("below", v)
        elif v in known:
            funnel.already_known += 1
            funnel.note("known", v)
        else:
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
) -> Funnel:
    """One run over the collected vacancies; returns its funnel statistics."""
    funnel = Funnel()
    funnel.count_collected(vacancies)
    new = select_new(vacancies, profile, tracker.known, funnel)
    # The sheet first: if Telegram fails afterwards, the vacancy is still logged and isn't
    # sent again next run. A missed message is better than the same message every 3 hours.
    tracker.append(new)
    to_send = [v for v in new if (v.score or 0) >= profile.notify_threshold]
    funnel.written, funnel.sheet_only = len(new), len(new) - len(to_send)
    for v in new:
        funnel.note("written", v)
    for i, v in enumerate(to_send[:limit]):
        if i:
            time.sleep(delay)
        try:
            notifier.send(v)
            funnel.sent += 1
            funnel.note("sent", v)
        except TelegramError as exc:
            funnel.failed += 1
            log.error("Telegram: %s @ %s not sent: %s", v.title, v.company, exc)
    for line in funnel.lines():
        log.info("Funnel: %s", line)
    write_summary(funnel)
    return funnel


def _print_matches(vacancies: Sequence[Vacancy], profile: Profile) -> None:
    funnel = Funnel()
    funnel.count_collected(vacancies)
    matches = select_new(vacancies, profile, KnownVacancies(), funnel)
    # A Windows console defaults to a legacy code page without "ó" or Cyrillic.
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    for v in matches:
        where = "telegram" if (v.score or 0) >= profile.notify_threshold else "sheet   "
        print(
            f"{v.score:>3} {where}  {v.title} @ {v.company}  | {location_format(v)}"
            f"  {salary_range(v)}"
        )
    sent = sum((v.score or 0) >= profile.notify_threshold for v in matches)
    print(
        f"\n{len(matches)} of {len(vacancies)} vacancies reach the sheet threshold "
        f"{profile.sheet_threshold}, {sent} of them the Telegram threshold "
        f"{profile.notify_threshold}"
    )
    # The sheet isn't read in a dry run: "already known" stays 0 and nothing is written.
    lines = [*funnel.lines()[:3], *funnel.source_lines()]
    print("\n".join(["", *(f"Funnel: {line}" for line in lines)]))


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
        try:
            tracker = SheetTracker(open_worksheet(settings.service_account, settings.sheet_id))
        except SheetError as exc:
            log.error("Sheet: %s", exc)
            return 2
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
            failures += run(collect_all(http), load_profile(), tracker, bot).failed
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
