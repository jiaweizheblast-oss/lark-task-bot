import datetime

import bot


def main():
    bot.PANEL_PASSWORD = "worker-presence-panel-password"
    bot.NEXUS_TALENT_WORKER_TOKEN = (
        "worker-presence-token-with-at-least-32-characters"
    )
    stored = {"row": None, "publication_queued": False}

    def upsert(worker_id, status, capabilities, version, started_at):
        stored["row"] = {
            "worker_id": worker_id,
            "status": status,
            "capabilities": dict(capabilities),
            "version": version,
            "started_at": started_at,
            "last_seen_at": datetime.datetime.now(datetime.timezone.utc),
        }
        return stored["row"]

    bot.db.upsert_talent_worker_presence = upsert
    bot.db.get_latest_talent_worker_presence = lambda: stored["row"]
    bot.db.get_talent_daily_publication_by_date = lambda _day: None
    bot.db.queue_talent_daily_publication = lambda *args: stored.update(
        publication_queued=True
    )

    client = bot.app.test_client()
    worker_headers = {
        "Authorization": f"Bearer {bot.NEXUS_TALENT_WORKER_TOKEN}",
    }
    payload = {
        "worker_id": "windows-presence-test",
        "status": "idle",
        "capabilities": {"search": True, "publication": True},
        "version": "windows-worker-v2",
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    assert client.post(
        "/api/integration/v1/talent/workers/heartbeat",
        json=payload,
    ).status_code == 401
    accepted = client.post(
        "/api/integration/v1/talent/workers/heartbeat",
        json=payload,
        headers=worker_headers,
    )
    assert accepted.status_code == 200
    assert accepted.get_json()["status"] == "accepted"

    assert client.get("/api/talent/worker-status").status_code == 401
    online = client.get(
        "/api/talent/worker-status",
        headers={"X-Auth": bot.PANEL_PASSWORD},
    )
    assert online.status_code == 200
    assert online.get_json()["online"] is True
    assert online.get_json()["capabilities"] == {
        "search": True,
        "publication": True,
    }

    stored["row"]["last_seen_at"] = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(seconds=bot.TALENT_WORKER_ONLINE_SECONDS + 1)
    )
    offline = client.get(
        "/api/talent/worker-status",
        headers={"X-Auth": bot.PANEL_PASSWORD},
    )
    assert offline.get_json()["online"] is False

    bot._kolkata_today = lambda: datetime.date(2026, 7, 23)
    blocked = client.post(
        "/api/talent/publications/today",
        json={"hr_names": ["JENNIFER"]},
        headers={"X-Auth": bot.PANEL_PASSWORD},
    )
    assert blocked.status_code == 409
    assert blocked.get_json()["error"] == "talent_worker_offline"
    assert stored["publication_queued"] is False

    print("Worker authenticated heartbeat: PASSED")
    print("Manager online/offline status: PASSED")
    print("Offline Worker blocks a dangling publication task: PASSED")


if __name__ == "__main__":
    main()
