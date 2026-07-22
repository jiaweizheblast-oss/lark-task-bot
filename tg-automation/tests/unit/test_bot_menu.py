from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from apps.bot.main import is_authorized, main_keyboard
from tests.unit.test_campaign_service import build_approved_campaign
from tg_automation.admin_bot.service import AdminBotService
from tg_automation.core.config import Settings


def test_internal_admin_bot_menu_stays_minimal() -> None:
    keyboard = main_keyboard()
    labels = [button.text for row in keyboard.inline_keyboard for button in row]

    assert labels == [
        "🕒 SCHEDULED",
        "👥 GROUPS",
        "📊 SENDING STATUS",
    ]
    assert len(keyboard.inline_keyboard) == 2
    assert all(button.url is None for row in keyboard.inline_keyboard for button in row)


def test_admin_user_ids_are_parsed_and_deduplicated() -> None:
    settings = Settings(app_env="test", telegram_admin_user_ids="1001, 1002,1001")

    assert settings.admin_user_ids == {1001, 1002}


def test_only_allowlisted_telegram_user_is_authorized() -> None:
    settings = Settings(app_env="test", telegram_admin_user_ids="1001")

    assert is_authorized(SimpleNamespace(effective_user=SimpleNamespace(id=1001)), settings)
    assert not is_authorized(SimpleNamespace(effective_user=SimpleNamespace(id=9999)), settings)
    assert not is_authorized(SimpleNamespace(effective_user=None), settings)


def test_invalid_admin_user_id_is_rejected_during_configuration() -> None:
    with pytest.raises(ValidationError, match="positive numeric user IDs"):
        Settings(app_env="test", telegram_admin_user_ids="1001,not-a-user-id")


def test_admin_bot_displays_scheduled_time_in_ist(session) -> None:
    campaign, service = build_approved_campaign(session)
    service.schedule(campaign.id, campaign.scheduled_at)

    text = AdminBotService(session).scheduled_text()

    assert "IST" in text
    assert " UTC" not in text
