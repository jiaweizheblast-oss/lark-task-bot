import copy
import hashlib
import hmac
from datetime import datetime, timezone

import bot
import talent_integration


KEY = "nexus-recruiting-route-key-at-least-32-bytes"


def signed_payload():
    content = {
        "visibility": "manager_only",
        "timezone_name": "Asia/Kolkata",
        "metrics": {"candidate_count": 1},
        "jobs": [{"core_job_ref": "job-route-001", "title": "Sales"}],
        "candidate_matches": [{
            "candidate_ref": "candidate-route-001",
            "match_ref": "match-route-001",
            "core_job_ref": "job-route-001",
            "score": "72",
            "score_visibility": "manager_only",
        }],
    }
    payload = {
        "schema_version": talent_integration.SCHEMA_VERSION,
        "source_system": talent_integration.SOURCE_SYSTEM,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "content_sha256": hashlib.sha256(
            talent_integration._canonical_json(content)
        ).hexdigest(),
        "content": content,
    }
    payload["signature"] = hmac.new(
        KEY.encode(),
        talent_integration._canonical_json(payload),
        hashlib.sha256,
    ).hexdigest()
    return payload


def main():
    bot.NEXUS_INTEGRATION_SIGNING_KEY = KEY
    bot.PANEL_PASSWORD = "route-test-panel-password"
    stored = []

    def store(value):
        if stored and stored[-1]["content_sha256"] == value["content_sha256"]:
            return {"accepted": False, "idempotent": True}
        stored.append(copy.deepcopy(value))
        return {"accepted": True, "idempotent": False}

    def latest():
        if not stored:
            return None
        value = stored[-1]
        now = datetime.now(timezone.utc)
        return {
            "content_sha256": value["content_sha256"],
            "schema_version": value["schema_version"],
            "source_system": value["source_system"],
            "generated_at": datetime.fromisoformat(value["generated_at"]),
            "content": value["content"],
            "received_at": now,
        }

    bot.db.store_talent_snapshot = store
    bot.db.get_latest_talent_snapshot = latest
    client = bot.app.test_client()
    payload = signed_payload()

    created = client.post(
        "/api/integration/v1/talent/snapshot",
        json=payload,
    )
    assert created.status_code == 201
    assert created.get_json()["accepted"] is True

    repeated = client.post(
        "/api/integration/v1/talent/snapshot",
        json=payload,
    )
    assert repeated.status_code == 200
    assert repeated.get_json()["idempotent"] is True

    tampered = copy.deepcopy(payload)
    tampered["content"]["candidate_matches"][0]["score"] = "99"
    rejected = client.post(
        "/api/integration/v1/talent/snapshot",
        json=tampered,
    )
    assert rejected.status_code == 422

    assert client.get("/api/talent/snapshot").status_code == 401
    visible = client.get(
        "/api/talent/snapshot",
        headers={"X-Auth": bot.PANEL_PASSWORD},
    )
    assert visible.status_code == 200
    body = visible.get_json()
    assert body["snapshot"]["content"]["visibility"] == "manager_only"
    assert body["snapshot"]["content_sha256"] == payload["content_sha256"]

    print("Nexus signed snapshot ingest route: PASSED")
    print("Idempotent replay and tampering controls: PASSED")
    print("Manager snapshot API authentication: PASSED")


if __name__ == "__main__":
    main()
