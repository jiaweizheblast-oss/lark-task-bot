from __future__ import annotations

from datetime import timedelta

from tg_automation.core.config import clear_settings_cache
from tg_automation.core.time import utc_now


def auth_headers(key: str, actor: str = "user-1") -> dict[str, str]:
    return {"X-NEXUS-API-KEY": key, "X-NEXUS-ACTOR": actor}


async def test_nexus_api_enforces_role_and_imports_once(client, monkeypatch) -> None:
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.setenv("NEXUS_OPERATOR_API_KEY", "operator-secret")
    monkeypatch.setenv("NEXUS_ADMIN_API_KEY", "admin-secret")
    clear_settings_cache()
    payload = {
        "external_event_id": "web-api-001",
        "content_type": "NEW_FEATURE",
        "title": "Faster withdrawals",
        "summary": "A clearer withdrawal status is now available.",
        "action_url": "https://app.21.game/features",
    }
    try:
        missing = await client.post("/api/v1/integrations/nexus/content-events", json=payload)
        invalid = await client.post(
            "/api/v1/integrations/nexus/content-events",
            json=payload,
            headers=auth_headers("invalid-secret"),
        )
        created = await client.post(
            "/api/v1/integrations/nexus/content-events",
            json=payload,
            headers=auth_headers("operator-secret", "nexus-editor"),
        )
        duplicate = await client.post(
            "/api/v1/integrations/nexus/content-events",
            json=payload,
            headers=auth_headers("operator-secret", "nexus-editor"),
        )
        destination = await client.post(
            "/api/v1/tg/destinations",
            json={
                "name": "NEXUS Website Draft Test",
                "telegram_chat_id": "-100998877",
                "destination_type": "TEST_CHANNEL",
                "source_code": "nexus_website_draft",
                "is_test": True,
            },
            headers=auth_headers("operator-secret", "nexus-editor"),
        )
        draft = await client.post(
            "/api/v1/integrations/nexus/content-events/web-api-001/campaign-draft",
            json={
                "destination_ids": [destination.json()["data"]["id"]],
                "scheduled_at": (utc_now() + timedelta(hours=2)).isoformat(),
            },
            headers=auth_headers("operator-secret", "nexus-editor"),
        )
        listed = await client.get(
            "/api/v1/integrations/nexus/content-events",
            headers=auth_headers("operator-secret", "nexus-operator"),
        )
        audit_denied = await client.get(
            "/api/v1/tg/audit-logs",
            headers=auth_headers("operator-secret", "nexus-operator"),
        )
        audits = await client.get(
            "/api/v1/tg/audit-logs?resource_type=content",
            headers=auth_headers("admin-secret", "nexus-admin"),
        )
    finally:
        clear_settings_cache()

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert created.status_code == 200
    assert created.json()["data"]["duplicate"] is False
    assert duplicate.json()["data"]["duplicate"] is True
    assert draft.status_code == 200
    assert draft.json()["data"]["status"] == "DRAFT"
    assert draft.json()["data"]["button_type"] == "VIEW_DETAILS"
    assert len(listed.json()["data"]) == 1
    assert listed.json()["data"][0]["payload"]["action_url"].startswith("https://app.21.game/")
    assert listed.json()["data"][0]["content_status"] == "WAITING_REVIEW"
    assert listed.json()["data"][0]["content_title"] == "Faster withdrawals"
    assert "NEW FEATURE" in listed.json()["data"][0]["telegram_caption"]
    assert audit_denied.status_code == 403
    assert audits.status_code == 200
    assert audits.json()["data"][0]["action"] == "NEXUS_CONTENT_IMPORTED"


async def test_auth_enabled_without_keys_fails_closed(client, monkeypatch) -> None:
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.delenv("NEXUS_OPERATOR_API_KEY", raising=False)
    monkeypatch.delenv("NEXUS_ADMIN_API_KEY", raising=False)
    clear_settings_cache()
    try:
        response = await client.get("/api/v1/tg/contents")
    finally:
        clear_settings_cache()

    assert response.status_code == 503
