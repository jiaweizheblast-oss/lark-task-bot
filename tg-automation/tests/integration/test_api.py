from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apps.api.main import app
from tg_automation.core.config import get_settings
from tg_automation.destinations.api import get_telegram_gateway
from tg_automation.telegram.schemas import TelegramPermissionResult, TelegramSendResult


class FakeDestinationGateway:
    async def check_permissions(self, chat_id: str) -> TelegramPermissionResult:
        return TelegramPermissionResult(
            chat_id=chat_id,
            chat_title="Automation Test Channel",
            chat_type="channel",
            can_post=True,
        )

    async def send_photo(self, chat_id, photo, caption, buttons) -> TelegramSendResult:
        return TelegramSendResult(chat_id=chat_id, message_id=1)


async def test_health_endpoint(client) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ok"


async def test_content_api_round_trip(client) -> None:
    response = await client.post(
        "/api/v1/tg/contents",
        json={
            "content_type": "NEW_GAME",
            "title": "New game",
            "caption": "A new game is online.",
        },
    )
    assert response.status_code == 200
    content_id = response.json()["data"]["id"]

    edited = await client.patch(
        f"/api/v1/tg/contents/{content_id}",
        json={"title": "Edited new game"},
    )
    approved = await client.post(f"/api/v1/tg/contents/{content_id}/approve")

    assert edited.json()["data"]["title"] == "Edited new game"
    assert approved.json()["data"]["status"] == "APPROVED"


async def test_unclassified_media_recommendation_api(client) -> None:
    created = await client.post(
        "/api/v1/tg/media",
        json={"name": "Promotion image", "file_path": "assets/promotion.jpg"},
    )
    response = await client.get("/api/v1/tg/media/recommend")

    assert created.status_code == 200
    assert response.status_code == 200
    assert response.json()["data"]["name"] == "Promotion image"
    assert "category" not in response.json()["data"]


async def test_destination_permission_check_and_test_send_guard(client, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "telegram_test_sending_enabled", False)
    created = await client.post(
        "/api/v1/tg/destinations",
        json={
            "name": "Permission Test Channel",
            "telegram_chat_id": "-1009876543210",
            "destination_type": "TEST_CHANNEL",
            "source_code": "permission_test",
            "is_test": True,
        },
    )
    destination_id = created.json()["data"]["id"]
    app.dependency_overrides[get_telegram_gateway] = lambda: FakeDestinationGateway()

    checked = await client.post(f"/api/v1/tg/destinations/{destination_id}/check-permissions")
    blocked_test = await client.post(
        f"/api/v1/tg/destinations/{destination_id}/send-test",
        json={"photo": "assets/test.jpg", "caption": "Preview"},
    )

    assert checked.status_code == 200
    assert checked.json()["data"]["bot_can_post"] is True
    assert blocked_test.status_code == 423
    assert blocked_test.json()["error"]["code"] == "TEST_SENDING_DISABLED"

    blocked_upload = await client.post(
        f"/api/v1/tg/destinations/{destination_id}/send-test-upload",
        data={"caption": "Browser preview", "buttons": "[]"},
        files={"photo": ("preview.jpg", b"not-a-real-image", "image/jpeg")},
    )
    assert blocked_upload.status_code == 423
    assert blocked_upload.json()["error"]["code"] == "TEST_SENDING_DISABLED"


async def test_nexus_can_read_templates_and_dashboard(client) -> None:
    templates = await client.get("/api/v1/tg/content-templates?content_type=LUCKY_SPIN")
    dashboard = await client.get("/api/v1/tg/dashboard?include_test=true")

    assert templates.status_code == 200
    assert templates.json()["data"][0]["id"] == "lucky-spin"
    assert dashboard.status_code == 200
    assert "destination_health" in dashboard.json()["data"]
    assert "daily_automation" not in dashboard.json()["data"]


async def test_nexus_can_schedule_only_a_test_destination(
    client, monkeypatch, tmp_path
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "telegram_test_sending_enabled", True)
    monkeypatch.setattr(settings, "media_storage_dir", str(tmp_path))
    created = await client.post(
        "/api/v1/tg/destinations",
        json={
            "name": "Scheduled Test Channel",
            "telegram_chat_id": "-1001234500000",
            "destination_type": "TEST_CHANNEL",
            "source_code": "scheduled_test",
            "is_test": True,
        },
    )
    destination_id = created.json()["data"]["id"]
    tomorrow = (datetime.now(ZoneInfo("Asia/Kolkata")) + timedelta(days=1)).date()
    response = await client.post(
        "/api/v1/tg/test-schedules",
        data={
            "destination_id": destination_id,
            "caption": "Scheduled public test",
            "schedule_date": tomorrow.isoformat(),
            "time_slot": "09:00",
            "button_label": "OPEN BOT",
            "button_url": "https://t.me/Game21HubBot",
        },
        files={
            "photo": (
                "preview.jpg",
                b"\xff\xd8\xff\xe0scheduled-test-image",
                "image/jpeg",
            )
        },
    )
    listed = await client.get("/api/v1/tg/test-schedules")

    assert response.status_code == 200
    assert response.json()["data"]["time_slot"] == "09:00"
    assert response.json()["data"]["destination_id"] == destination_id
    assert response.json()["data"]["delivery_status"] == "PENDING"
    assert listed.json()["data"][0]["caption"] == "Scheduled public test"
    assert len(list(tmp_path.glob("scheduled-*.jpg"))) == 1
