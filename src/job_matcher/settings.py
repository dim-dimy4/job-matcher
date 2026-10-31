"""Secrets for the outputs (SPEC §11): GitHub Secrets in CI, a git-ignored .env locally.

Error messages name the variables but never print their values: CI logs are public.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REQUIRED = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "GOOGLE_SERVICE_ACCOUNT_JSON", "SHEET_ID")


class SettingsError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    # repr=False: an accidental print(settings) or a traceback doesn't show the secrets.
    telegram_token: str = field(repr=False)
    telegram_chat_id: str
    service_account: dict[str, Any] = field(repr=False)
    sheet_id: str


def _service_account(value: str) -> dict[str, Any]:
    """The key itself (CI secret) or a path to the key file (handier in a local .env)."""
    text = value if value.lstrip().startswith(("{", "[")) else _read_key_file(Path(value))
    try:
        info = json.loads(text)
    except json.JSONDecodeError:
        raise SettingsError("GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON") from None
    if not isinstance(info, dict):
        raise SettingsError("GOOGLE_SERVICE_ACCOUNT_JSON must be a JSON object")
    return info


def _read_key_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        raise SettingsError("GOOGLE_SERVICE_ACCOUNT_JSON: key file not found") from None


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    missing = [name for name in REQUIRED if not env.get(name, "").strip()]
    if missing:
        raise SettingsError(f"missing environment variables: {', '.join(missing)}")
    return Settings(
        telegram_token=env["TELEGRAM_BOT_TOKEN"].strip(),
        telegram_chat_id=env["TELEGRAM_CHAT_ID"].strip(),
        service_account=_service_account(env["GOOGLE_SERVICE_ACCOUNT_JSON"]),
        sheet_id=env["SHEET_ID"].strip(),
    )
