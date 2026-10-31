"""Secrets from the environment; errors name the variable but never show its value."""

import json

import pytest

from job_matcher.settings import REQUIRED, SettingsError, load_settings

KEY = {"type": "service_account", "client_email": "bot@example.iam.gserviceaccount.com"}


@pytest.fixture
def env():
    return {
        "TELEGRAM_BOT_TOKEN": "123:secret-token",
        "TELEGRAM_CHAT_ID": " 42 ",
        "GOOGLE_SERVICE_ACCOUNT_JSON": json.dumps(KEY),
        "SHEET_ID": "sheet-id",
    }


def test_settings_from_environment(env):
    settings = load_settings(env)

    assert settings.telegram_token == "123:secret-token"
    assert settings.telegram_chat_id == "42", "whitespace from a pasted value is stripped"
    assert settings.service_account == KEY
    assert settings.sheet_id == "sheet-id"


def test_repr_hides_secrets(env):
    text = repr(load_settings(env))

    assert "secret-token" not in text
    assert "client_email" not in text


def test_service_account_from_a_key_file(env, tmp_path):
    path = tmp_path / "bot.service-account.json"
    path.write_text(json.dumps(KEY), encoding="utf-8")

    assert load_settings({**env, "GOOGLE_SERVICE_ACCOUNT_JSON": str(path)}).service_account == KEY


@pytest.mark.parametrize("name", REQUIRED)
@pytest.mark.parametrize("value", [None, "", "   "], ids=["unset", "empty", "blank"])
def test_missing_variable_is_named(env, name, value):
    if value is None:
        del env[name]
    else:
        env[name] = value

    with pytest.raises(SettingsError, match=f"missing environment variables: {name}$"):
        load_settings(env)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ('{"private_key": "abc', "not valid JSON"),
        ('["private_key"]', "must be a JSON object"),
        ("no/such/key.json", "key file not found"),
    ],
    ids=["broken-json", "not-an-object", "missing-file"],
)
def test_bad_service_account_does_not_leak_the_value(env, value, message):
    with pytest.raises(SettingsError, match=message) as error:
        load_settings({**env, "GOOGLE_SERVICE_ACCOUNT_JSON": value})

    assert "private_key" not in str(error.value)
    assert error.value.__cause__ is None, "the JSON parser's error quotes the input"
