from __future__ import annotations

from tg_automation.content_templates.catalog import CONTENT_PRESETS, get_preset, list_presets
from tg_automation.storage.enums import ContentType


def test_template_catalog_has_unique_safe_presets() -> None:
    ids = [item["id"] for item in CONTENT_PRESETS]

    assert len(ids) == len(set(ids))
    assert all(len(item["caption_template"]) <= 1024 for item in CONTENT_PRESETS)


def test_template_catalog_filters_by_content_type() -> None:
    new_games = list_presets(ContentType.NEW_GAME)

    assert [item["id"] for item in new_games] == ["new-game-launch"]
    assert get_preset("new-game-launch")["content_type"] == "NEW_GAME"
    assert get_preset("does-not-exist") is None


def test_common_marketing_content_types_have_simple_templates() -> None:
    expected = {
        ContentType.DEPOSIT_BONUS: "deposit-bonus",
        ContentType.WELCOME_BONUS: "welcome-bonus",
        ContentType.VIP_BONUS: "vip-bonus",
        ContentType.INDUSTRY_CONTENT: "industry-content",
    }

    for content_type, preset_id in expected.items():
        assert list_presets(content_type) == []
        item = get_preset(preset_id)
        assert item["content_type"] == content_type.value
        assert len(item["recommended_buttons"]) == 1
    assert get_preset("industry-content")["automation_policy"] == "MANUAL_REVIEW_REQUIRED"


def test_first_version_catalog_hides_deferred_content_types() -> None:
    visible_types = {item["content_type"] for item in list_presets()}

    assert "GIFT_CODE" not in visible_types
    assert "NEW_GAME" in visible_types
    assert "LUCKY_SPIN" in visible_types
    assert "WELCOME_BONUS" not in visible_types
    assert "VIP_BONUS" not in visible_types
    assert "DEPOSIT_BONUS" not in visible_types
    assert "EMERGENCY_NOTICE" not in visible_types
    assert "INDUSTRY_CONTENT" not in visible_types
