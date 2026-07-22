from __future__ import annotations

import pytest
from pydantic import ValidationError

from tg_automation.core.config import Settings
from tg_automation.core.logging import redacted_config


def test_secret_values_are_redacted() -> None:
    settings = Settings(
        app_env="test",
        telegram_bot_token="123456:secret",
        telegram_webhook_secret="hook-secret",
    )

    output = redacted_config(settings)

    assert output["telegram_bot_token"] == "[REDACTED]"
    assert output["telegram_webhook_secret"] == "[REDACTED]"
    assert "123456:secret" not in str(output)


def test_production_configuration_fails_closed() -> None:
    with pytest.raises(ValidationError, match="require NEXUS API authentication"):
        Settings(app_env="production", tracking_base_url="https://go.21.game/r")

    with pytest.raises(ValidationError, match="must use HTTPS"):
        Settings(
            app_env="production",
            api_auth_enabled=True,
            nexus_admin_api_key="a-secure-admin-key-over-24-characters",
            tracking_base_url="http://go.21.game/r",
        )


def test_sending_requires_bot_token() -> None:
    with pytest.raises(ValidationError, match="without a Bot Token"):
        Settings(app_env="test", global_sending_enabled=True, telegram_bot_token=None)


def test_api_keys_cannot_be_reused_across_roles() -> None:
    with pytest.raises(ValidationError, match="must be unique"):
        Settings(
            app_env="test",
            nexus_operator_api_key="same-key",
            nexus_admin_api_key="same-key",
        )


def test_blank_optional_secrets_are_treated_as_unset() -> None:
    settings = Settings(
        app_env="development",
        telegram_bot_token="",
        telegram_webhook_secret="   ",
        nexus_operator_api_key="",
        nexus_admin_api_key="",
    )

    assert settings.telegram_bot_token is None
    assert settings.telegram_webhook_secret is None
    assert settings.nexus_operator_api_key is None
    assert settings.nexus_admin_api_key is None
