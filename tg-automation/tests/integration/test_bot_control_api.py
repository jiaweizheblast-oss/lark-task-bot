from __future__ import annotations

from tg_automation.core.config import clear_settings_cache


def headers(key: str, actor: str) -> dict[str, str]:
    return {"X-NEXUS-API-KEY": key, "X-NEXUS-ACTOR": actor}


async def test_website_can_view_internal_admin_bot(client, monkeypatch) -> None:
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.setenv("NEXUS_OPERATOR_API_KEY", "operator-key")
    monkeypatch.setenv("TELEGRAM_ADMIN_USER_IDS", "1001,1002")
    monkeypatch.setenv("NEXUS_ADMIN_URL", "https://nexus.example.com")
    clear_settings_cache()
    try:
        overview = await client.get(
            "/api/v1/tg/bot-control/overview",
            headers=headers("operator-key", "operator-1"),
        )
    finally:
        clear_settings_cache()

    assert overview.status_code == 200
    data = overview.json()["data"]
    assert data["admin_bot"]["menu"] == [
        "CREATE_OR_SEND",
        "SCHEDULED_TASKS",
        "GROUPS_AND_CHANNELS",
        "SENDING_STATUS",
    ]
    assert data["admin_bot"]["authorised_admin_count"] == 2
