from datetime import date, datetime, timedelta, timezone

import bot


PANEL_PASSWORD = "panel-password-for-search-route-test"
WORKER_TOKEN = "worker-token-for-search-route-test-at-least-32"


def main():
    bot.PANEL_PASSWORD = PANEL_PASSWORD
    bot.NEXUS_TALENT_WORKER_TOKEN = WORKER_TOKEN
    bot._kolkata_today = lambda: date(2026, 7, 23)
    stored = {}
    settings = {}
    publication_calls = []

    bot.db.get_job_request_by_core_ref = lambda ref: {
        "core_job_ref": ref,
        "status": "open",
    }
    bot.db.get_job_request_by_ref = lambda ref: {
        "job_ref": ref,
        "record_type": "operational",
        "status": "open",
        "search_profile_ref": "job-core-001",
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
    bot.db.get_talent_daily_publication_by_date = lambda _day: None
    bot.db.get_setting = lambda key: settings.get(key)
    bot.db.set_setting = lambda key, value: settings.__setitem__(key, value)
    bot.db.list_talent_search_tasks = lambda limit=50: list(stored.values())
    bot.db.claim_talent_search_task = lambda worker, lease: (
        next(iter(stored.values())),
        "route-test-lease-token-with-at-least-32-characters",
    )
    bot.db.heartbeat_talent_search_task = lambda *args: True
    bot.db.get_talent_search_task = lambda task_id: stored.get(task_id)
    retry_calls = []
    def retry_failed(task_id):
        retry_calls.append(task_id)
        stored[task_id]["status"] = "pending"
        stored[task_id]["last_error_code"] = None
        return stored[task_id]
    bot.db.retry_failed_talent_search_task = retry_failed
    def complete(*args):
        result = args[3]
        task_id = args[0]
        stored[task_id]["status"] = "succeeded"
        stored[task_id]["result"] = result
        stored[task_id]["result_sha256"] = args[4]
        stored[task_id]["publication_status"] = "ready"
        return {"status": "succeeded", "idempotent": False}

    bot.db.complete_talent_search_task = complete
    bot.db.fail_talent_search_task = lambda *args: True
    bot._queue_talent_search_publication = lambda task_id: (
        publication_calls.append(task_id)
        or {
            "ok": True, "status": "queued", "expected_rows": 100,
            "lark_calls": 0,
        }
    )
    reset_calls = []
    bot.db.reset_talent_daily_publication = lambda publication_id: (
        reset_calls.append(publication_id)
        or {"status": "reset", "reset": True, "task_count": 1}
    )

    client = bot.app.test_client()
    command = {
        "task_id": "82f78cc2-62f2-4e32-bc64-866575136fdd",
        "operational_job_ref": "REQ-20260722-TEST0001",
        "core_job_ref": "job-core-001",
        "requested_contact_count": 100,
        "max_review_pool_count": 10,
        "auto_publish": True,
        "hr_allocations": [
            {"name": "Asha", "count": 60},
            {"name": "Neha", "count": 40},
        ],
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

    repeated_command = dict(command)
    repeated_command["task_id"] = "be3aab9e-4f0c-4cc1-a2fb-c65e8e3d39cc"
    repeated = client.post(
        "/api/talent/search-tasks",
        json=repeated_command,
        headers={"X-Auth": PANEL_PASSWORD},
    )
    assert repeated.status_code == 200
    assert repeated.get_json()["idempotent"] is True
    assert len(stored) == 1
    assert repeated.get_json()["task"]["business_date"] == "2026-07-23"

    conflicting_command = dict(command)
    conflicting_command["task_id"] = "0a3c7abc-05c3-435b-b198-19351f1b09af"
    conflicting_command["requested_contact_count"] = 80
    conflicting_command["hr_allocations"] = [
        {"name": "Asha", "count": 40},
        {"name": "Neha", "count": 40},
    ]
    conflicting = client.post(
        "/api/talent/search-tasks",
        json=conflicting_command,
        headers={"X-Auth": PANEL_PASSWORD},
    )
    assert conflicting.status_code == 409
    assert conflicting.get_json()["error"] == "daily_search_already_running"
    assert len(stored) == 1

    stored[command["task_id"]]["status"] = "succeeded"
    stored[command["task_id"]]["publication_status"] = "ready"
    frozen_repeat = client.post(
        "/api/talent/search-tasks",
        json=repeated_command,
        headers={"X-Auth": PANEL_PASSWORD},
    )
    assert frozen_repeat.status_code == 200
    assert frozen_repeat.get_json()["idempotent"] is True
    assert len(stored) == 1
    frozen_conflict = client.post(
        "/api/talent/search-tasks",
        json=conflicting_command,
        headers={"X-Auth": PANEL_PASSWORD},
    )
    assert frozen_conflict.status_code == 409
    assert frozen_conflict.get_json()["error"] == "daily_search_already_running"
    assert len(stored) == 1
    stored[command["task_id"]]["status"] = "pending"
    stored[command["task_id"]]["publication_status"] = "not_ready"

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
        "operational_job_ref": task["operational_job_ref"],
        "core_job_ref": task["core_job_ref"],
        "quota_fulfilled": True,
        "applicable": True,
        "selected_contacts": 100,
        "hr_allocations": task["hr_allocations"],
        "frozen_bundle": {
            "plan_id": "086f7b6a-20a2-4a23-83dd-b3288d4df846",
            "plan_sha256": "c" * 64,
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat(),
            "stored_on_worker": True,
        },
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
    assert publication_calls == [task["task_id"]]
    assert completed.get_json()["publication"]["status"] == "queued"
    assert completed.get_json()["publication"]["lark_calls"] == 0

    publication_calls.clear()
    queued = client.post(
        f"/api/talent/search-tasks/{task['task_id']}/publish",
        headers={"X-Auth": PANEL_PASSWORD},
    )
    assert queued.status_code == 200
    assert queued.get_json()["status"] == "queued"
    assert publication_calls == [task["task_id"]]

    reset_url = "/api/talent/publications/22222222-2222-4222-8222-222222222222/reset"
    assert client.post(reset_url).status_code == 401
    assert client.post(
        reset_url,
        json={"confirm": "wrong"},
        headers={"X-Auth": PANEL_PASSWORD},
    ).status_code == 422
    reset = client.post(
        reset_url,
        json={"confirm": "RESET_UNPUBLISHED"},
        headers={"X-Auth": PANEL_PASSWORD},
    )
    assert reset.status_code == 200
    assert reset.get_json() == {
        "status": "reset", "reset": True, "task_count": 1,
    }
    assert reset_calls == ["22222222-2222-4222-8222-222222222222"]

    stored[task["task_id"]]["status"] = "failed"
    stored[task["task_id"]]["last_error_code"] = "workspace_not_clean"
    retry_url = f"/api/talent/search-tasks/{task['task_id']}/retry"
    assert client.post(retry_url).status_code == 401
    assert client.post(
        retry_url,
        json={"confirm": "wrong"},
        headers={"X-Auth": PANEL_PASSWORD},
    ).status_code == 422
    retried = client.post(
        retry_url,
        json={"confirm": "RETRY_FAILED"},
        headers={"X-Auth": PANEL_PASSWORD},
    )
    assert retried.status_code == 200
    assert retried.get_json()["status"] == "pending"
    assert retry_calls == [task["task_id"]]

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
    print("Repeated one-click search is idempotent: PASSED")
    print("Conflicting second search while today's run is active: REJECTED")
    print("Unauthorized and unsafe worker submissions: REJECTED")


if __name__ == "__main__":
    main()
