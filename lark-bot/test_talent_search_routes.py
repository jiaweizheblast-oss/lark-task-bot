from datetime import datetime, timezone

import bot


PANEL_PASSWORD = "panel-password-for-search-route-test"
WORKER_TOKEN = "worker-token-for-search-route-test-at-least-32"


def main():
    bot.PANEL_PASSWORD = PANEL_PASSWORD
    bot.NEXUS_TALENT_WORKER_TOKEN = WORKER_TOKEN
    stored = {}

    bot.db.get_job_request_by_core_ref = lambda ref: {
        "core_job_ref": ref,
        "status": "open",
    }

    def enqueue(task):
        row = {
            **task,
            "payload": task,
            "status": "pending",
            "result_sha256": None,
            "worker_id": None,
            "attempt_count": 0,
            "last_error_code": None,
            "created_at": datetime.fromisoformat(task["created_at"]),
            "updated_at": datetime.now(timezone.utc),
            "expires_at": datetime.fromisoformat(task["expires_at"]),
            "claimed_at": None,
            "lease_expires_at": None,
            "result": None,
        }
        inserted = task["task_id"] not in stored
        stored[task["task_id"]] = row
        return row, inserted

    bot.db.enqueue_talent_search_task = enqueue
    bot.db.list_talent_search_tasks = lambda limit=50: list(stored.values())
    bot.db.claim_talent_search_task = lambda worker, lease: (
        next(iter(stored.values())),
        "route-test-lease-token-with-at-least-32-characters",
    )
    bot.db.heartbeat_talent_search_task = lambda *args: True
    bot.db.get_talent_search_task = lambda task_id: stored.get(task_id)
    bot.db.complete_talent_search_task = lambda *args: {
        "status": "succeeded",
        "idempotent": False,
    }
    bot.db.fail_talent_search_task = lambda *args: True

    client = bot.app.test_client()
    command = {
        "task_id": "82f78cc2-62f2-4e32-bc64-866575136fdd",
        "core_job_ref": "job-core-001",
        "requested_contact_count": 100,
        "max_review_pool_count": 10,
        "budgets": {"max_total_observations": 800},
    }
    assert client.post("/api/talent/search-tasks", json=command).status_code == 401
    created = client.post(
        "/api/talent/search-tasks",
        json=command,
        headers={"X-Auth": PANEL_PASSWORD},
    )
    assert created.status_code == 201
    task = created.get_json()["task"]["payload"]

    worker_headers = {"Authorization": f"Bearer {WORKER_TOKEN}"}
    unauthorized = client.post(
        "/api/integration/v1/talent/search-tasks/claim",
        json={"worker_id": "windows-test", "lease_seconds": 120},
    )
    assert unauthorized.status_code == 401
    claimed = client.post(
        "/api/integration/v1/talent/search-tasks/claim",
        json={"worker_id": "windows-test", "lease_seconds": 120},
        headers=worker_headers,
    )
    assert claimed.status_code == 200
    assert claimed.get_json()["task"]["payload_sha256"] == task["payload_sha256"]

    result = {
        "schema_version": "talent-search-result-v1",
        "task_id": task["task_id"],
        "core_job_ref": task["core_job_ref"],
        "quota_fulfilled": True,
        "applicable": True,
        "database_changed": False,
        "daily_batches_created": 0,
        "review_tasks_created": 0,
        "lark_calls": 0,
        "contactout_calls": 0,
        "linkedin_profile_pages_opened": 0,
    }
    completed = client.post(
        f"/api/integration/v1/talent/search-tasks/{task['task_id']}/complete",
        json={
            "worker_id": "windows-test",
            "lease_token": "route-test-lease-token-with-at-least-32-characters",
            "result": result,
        },
        headers=worker_headers,
    )
    assert completed.status_code == 200

    unsafe = dict(result)
    unsafe["database_changed"] = True
    rejected = client.post(
        f"/api/integration/v1/talent/search-tasks/{task['task_id']}/complete",
        json={
            "worker_id": "windows-test",
            "lease_token": "route-test-lease-token-with-at-least-32-characters",
            "result": unsafe,
        },
        headers=worker_headers,
    )
    assert rejected.status_code == 422

    print("Nexus manager queue and worker routes: PASSED")
    print("Unauthorized and unsafe worker submissions: REJECTED")


if __name__ == "__main__":
    main()
