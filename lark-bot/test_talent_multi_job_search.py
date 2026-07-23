from datetime import date, datetime, timezone

import bot


PANEL_PASSWORD = "panel-password-for-multi-job-test"
WORKER_TOKEN = "worker-token-for-multi-job-test-at-least-32"


def main():
    bot.PANEL_PASSWORD = PANEL_PASSWORD
    bot.NEXUS_TALENT_WORKER_TOKEN = WORKER_TOKEN
    bot._kolkata_today = lambda: date(2026, 7, 24)
    settings = {}
    saved = []
    profiles = {
        "profile-sales": {"core_job_ref": "profile-sales", "status": "open"},
        "profile-service": {
            "core_job_ref": "profile-service",
            "status": "open",
        },
    }
    jobs = {
        "REQ-SALES-MEMBER": {
            "job_ref": "REQ-SALES-MEMBER",
            "record_type": "operational",
            "status": "open",
            "search_profile_ref": "profile-sales",
        },
        "REQ-SALES-LEAD": {
            "job_ref": "REQ-SALES-LEAD",
            "record_type": "operational",
            "status": "open",
            "search_profile_ref": "profile-sales",
        },
        "REQ-SERVICE": {
            "job_ref": "REQ-SERVICE",
            "record_type": "operational",
            "status": "open",
            "search_profile_ref": "profile-service",
        },
    }
    bot.db.get_job_request_by_core_ref = profiles.get
    bot.db.get_job_request_by_ref = jobs.get
    bot.db.get_talent_daily_publication_by_date = lambda _day: None
    bot.db.list_talent_search_tasks = lambda limit=500: []
    bot.db.get_latest_talent_worker_presence = lambda: {
        "worker_id": "windows-test",
        "status": "idle",
        "capabilities": {
            "search": True,
            "publication": True,
            "search_browser_ready": True,
        },
        "version": "multi-job-test",
        "started_at": datetime.now(timezone.utc),
        "last_seen_at": datetime.now(timezone.utc),
    }
    bot.db.get_setting = settings.get
    bot.db.set_setting = settings.__setitem__

    def enqueue_batch(tasks, search_run_id):
        rows = []
        for index, task in enumerate(tasks, start=1):
            row = {
                **task,
                "payload": task,
                "status": "pending",
                "search_run_id": search_run_id,
                "search_run_order": index,
                "search_run_size": len(tasks),
                "progress_phase": "queued",
                "progress_percent": 0,
                "progress_message": "Waiting for the local Worker",
                "progress_counts": {},
                "created_at": datetime.fromisoformat(task["created_at"]),
                "updated_at": datetime.now(timezone.utc),
                "expires_at": datetime.fromisoformat(task["expires_at"]),
            }
            rows.append(row)
        saved.extend(rows)
        return rows, len(rows)

    bot.db.enqueue_talent_search_batch = enqueue_batch
    client = bot.app.test_client()
    response = client.post(
        "/api/talent/search-batches",
        headers={"X-Auth": PANEL_PASSWORD},
        json={
            "jobs": [
                {
                    "operational_job_ref": "REQ-SALES-MEMBER",
                    "core_job_ref": "profile-sales",
                },
                {
                    "operational_job_ref": "REQ-SALES-LEAD",
                    "core_job_ref": "profile-sales",
                },
                {
                    "operational_job_ref": "REQ-SERVICE",
                    "core_job_ref": "profile-service",
                },
            ],
            "hr_allocations": [
                {"name": "JENNIFER", "count": 12},
                {"name": "SANDRINE", "count": 8},
            ],
        },
    )
    assert response.status_code == 201, response.get_json()
    body = response.get_json()
    assert body["selected_job_count"] == 3
    assert body["search_family_count"] == 2
    assert body["requested_contact_count"] == 20
    assert len(saved) == 2
    assert {row["core_job_ref"] for row in saved} == {
        "profile-sales", "profile-service",
    }
    assert sum(
        row["payload"]["requested_contact_count"] for row in saved
    ) == 20
    assert {
        row["payload"]["requested_contact_count"] for row in saved
    } == {10}
    assert all(row["search_run_id"] == body["search_run_id"] for row in saved)

    heartbeat_calls = []
    bot.db.heartbeat_talent_search_task = (
        lambda *args: heartbeat_calls.append(args) or True
    )
    headers = {"Authorization": f"Bearer {WORKER_TOKEN}"}
    progress = {
        "phase": "scanning_public_sources",
        "percent": 45,
        "message": "Scanning public evidence",
        "counts": {"observations": 18},
    }
    heartbeat = client.post(
        "/api/integration/v1/talent/search-tasks/"
        + saved[0]["task_id"]
        + "/heartbeat",
        headers=headers,
        json={
            "worker_id": "windows-test",
            "lease_token": "lease-token-with-at-least-32-characters",
            "lease_seconds": 120,
            "progress": progress,
        },
    )
    assert heartbeat.status_code == 200
    assert heartbeat_calls[-1][-1] == progress
    invalid = client.post(
        "/api/integration/v1/talent/search-tasks/"
        + saved[0]["task_id"]
        + "/heartbeat",
        headers=headers,
        json={
            "worker_id": "windows-test",
            "lease_token": "lease-token-with-at-least-32-characters",
            "lease_seconds": 120,
            "progress": {**progress, "percent": 100},
        },
    )
    assert invalid.status_code == 422

    print("Three operational jobs grouped into two search families: PASSED")
    print("Overall HR target preserved across family tasks: PASSED")
    print("Worker phase progress validation: PASSED")


if __name__ == "__main__":
    main()
