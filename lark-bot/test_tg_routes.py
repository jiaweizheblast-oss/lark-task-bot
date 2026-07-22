import io
import json

import bot


class FakeResponse:
    def __init__(self, *, ok=True, status_code=200):
        self.ok = ok
        self.status_code = status_code


def main():
    bot.PANEL_PASSWORD = "tg-route-test-password"
    calls = []

    def fake_tg_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path == "/health":
            return FakeResponse(), {
                "data": {
                    "status": "ok",
                    "environment": "test",
                    "sending_enabled": False,
                }
            }
        if path == "/api/v1/tg/destinations":
            return FakeResponse(), {
                "data": [
                    {
                        "id": "test-channel-id",
                        "name": "Test Channel",
                        "destination_type": "TEST_CHANNEL",
                        "is_test": True,
                        "status": "ENABLED",
                    },
                    {
                        "id": "production-channel-id",
                        "name": "Production Channel",
                        "destination_type": "CHANNEL",
                        "is_test": False,
                        "status": "ENABLED",
                    },
                ]
            }
        if path.endswith("/send-test-upload"):
            return FakeResponse(), {"data": {"chat_id": "-1001", "message_id": 42}}
        if path == "/api/v1/tg/test-schedules" and method == "GET":
            return FakeResponse(), {"data": []}
        if path == "/api/v1/tg/test-schedules" and method == "POST":
            return FakeResponse(), {
                "data": {
                    "schedule_date": "2026-07-24",
                    "time_slot": "09:00",
                    "destination_name": "Test Channel",
                }
            }
        raise AssertionError(path)

    bot._tg_request = fake_tg_request
    client = bot.app.test_client()
    auth = {"X-Auth": bot.PANEL_PASSWORD}

    assert client.get("/api/tg/status").status_code == 401
    status = client.get("/api/tg/status", headers=auth)
    assert status.status_code == 200
    assert status.get_json()["connected"] is True
    assert status.get_json()["sending_enabled"] is False

    destinations = client.get("/api/tg/destinations", headers=auth)
    assert destinations.status_code == 200
    rows = destinations.get_json()["destinations"]
    assert [row["id"] for row in rows] == ["test-channel-id"]

    sent = client.post(
        "/api/tg/send-test",
        headers=auth,
        data={
            "destination_id": "test-channel-id",
            "caption": "Website test",
            "button_label": "OPEN BOT",
            "button_url": "https://t.me/Game21HubBot",
            "photo": (io.BytesIO(b"image-bytes"), "preview.jpg"),
        },
        content_type="multipart/form-data",
    )
    assert sent.status_code == 200
    assert sent.get_json()["result"] == {"chat_id": "-1001", "message_id": 42}
    assert calls[-1][1].endswith("test-channel-id/send-test-upload")
    forwarded_buttons = json.loads(calls[-1][2]["data"]["buttons"])
    assert forwarded_buttons == [
        {
            "label": "OPEN BOT",
            "value": "https://t.me/Game21HubBot",
            "row": 0,
            "position": 0,
        }
    ]

    schedules = client.get("/api/tg/schedules", headers=auth)
    assert schedules.status_code == 200
    assert schedules.get_json()["schedules"] == []

    scheduled = client.post(
        "/api/tg/schedules",
        headers=auth,
        data={
            "destination_id": "test-channel-id",
            "caption": "Scheduled website test",
            "schedule_date": "2026-07-24",
            "time_slot": "09:00",
            "button_label": "OPEN BOT",
            "button_url": "https://t.me/Game21HubBot",
            "photo": (io.BytesIO(b"image-bytes"), "preview.jpg"),
        },
        content_type="multipart/form-data",
    )
    assert scheduled.status_code == 200
    assert scheduled.get_json()["result"]["time_slot"] == "09:00"
    assert calls[-1][1] == "/api/v1/tg/test-schedules"

    print("TG website routes: PASS")


if __name__ == "__main__":
    main()
