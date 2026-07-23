import copy
from datetime import datetime, timedelta, timezone

import bot


def main():
    task_id = "4cf3dd62-f303-413a-bd18-82af660cb13f"
    second_task_id = "df78f214-7e8d-4f7e-b6a1-e6bf5d0fb1f1"
    now = datetime.now(timezone.utc)
    state = {
        "task_id": task_id,
        "revision": 1,
        "status": "succeeded",
        "core_job_ref": "job-core-001",
        "payload_sha256": "a" * 64,
        "result_sha256": "b" * 64,
        "payload": {
            "created_at": now.isoformat(),
            "operational_job_ref": "REQ-20260722-TEST0001",
            "requested_contact_count": 2,
            "hr_allocations": [
                {"name": "Asha", "count": 1},
                {"name": "Neha", "count": 1},
            ],
        },
        "result": {
            "schema_version": "talent-search-result-v1",
            "task_id": task_id,
            "operational_job_ref": "REQ-20260722-TEST0001",
            "core_job_ref": "job-core-001",
            "selected_contacts": 2,
            "hr_allocations": [
                {"name": "Asha", "count": 1},
                {"name": "Neha", "count": 1},
            ],
            "quota_fulfilled": True,
            "applicable": True,
            "frozen_bundle": {
                "plan_id": "26084a68-ed53-4f5f-a1d7-fc330b229cb8",
                "plan_sha256": "c" * 64,
                "expires_at": (now + timedelta(hours=6)).isoformat(),
                "stored_on_worker": True,
            },
            "database_changed": False,
            "daily_batches_created": 0,
            "review_tasks_created": 0,
            "lark_calls": 0,
            "contactout_calls": 0,
            "linkedin_profile_pages_opened": 0,
        },
        "publication_status": "ready",
        "publication": {},
    }
    second_state = copy.deepcopy(state)
    second_state.update({
        "task_id": second_task_id,
        "core_job_ref": "job-core-002",
        "payload_sha256": "d" * 64,
        "result_sha256": "e" * 64,
    })
    second_state["payload"].update({
        "operational_job_ref": "REQ-20260722-TEST0002",
        "requested_contact_count": 1,
        "hr_allocations": [{"name": "Asha", "count": 1}],
    })
    second_state["result"].update({
        "task_id": second_task_id,
        "operational_job_ref": "REQ-20260722-TEST0002",
        "core_job_ref": "job-core-002",
        "selected_contacts": 1,
        "hr_allocations": [{"name": "Asha", "count": 1}],
        "frozen_bundle": {
            "plan_id": "8cb536fb-ef02-4cbe-b301-ff2c6e4888ef",
            "plan_sha256": "f" * 64,
            "expires_at": (now + timedelta(hours=8)).isoformat(),
            "stored_on_worker": True,
        },
    })
    queued_commands = []
    lark_calls = []
    pending_state = copy.deepcopy(second_state)
    pending_state["task_id"] = "7c07ec35-2367-4349-bb70-3147401031fd"
    pending_state["status"] = "pending"
    pending_state["publication_status"] = "not_ready"

    bot.db.get_talent_search_task = lambda _task_id: copy.deepcopy(state)
    listed_states = [
        copy.deepcopy(state),
        copy.deepcopy(second_state),
        copy.deepcopy(pending_state),
    ]
    bot.db.list_talent_search_tasks = lambda limit=500: copy.deepcopy(listed_states)
    bot.db.get_job_request_by_ref = lambda job_ref: {
        "job_ref": job_ref,
        "record_type": "operational",
        "status": "open",
        "search_profile_ref": (
            "job-core-001"
            if job_ref == "REQ-20260722-TEST0001"
            else "job-core-002"
        ),
        "title": (
            "Customer Service Representative"
            if job_ref == "REQ-20260722-TEST0001"
            else "Sales Member"
        ),
    }

    def queue(publication_id, business_date, command, task_ids):
        queued_commands.append(copy.deepcopy(command))
        assert publication_id == command["publication_id"]
        assert str(business_date) == command["business_date"]
        assert task_ids == [task_id, second_task_id]
        state["publication_status"] = "queued"
        state["publication"] = {
            "publication_id": publication_id,
            "business_date": str(business_date),
        }
        return {"status": "queued", "payload": copy.deepcopy(command)}

    bot.db.queue_talent_daily_publication = queue
    bot.lark_bitable.batch_create_recruiting_records = lambda *args, **kwargs: (
        lark_calls.append((args, kwargs))
    )

    try:
        bot._queue_talent_search_publication(task_id)
    except ValueError as error:
        assert "every search created today" in str(error)
    else:
        raise AssertionError("A daily workbook was queued while a search was running")
    listed_states.pop()
    first = bot._queue_talent_search_publication(task_id)
    assert first["status"] == "queued"
    assert first["requires_local_worker"] is True
    assert len(queued_commands) == 1
    assert "publication_manifest" not in queued_commands[0]
    assert "candidate" not in " ".join(queued_commands[0]).casefold()
    assert len(queued_commands[0]["cohorts"]) == 2
    assert queued_commands[0]["total_contact_count"] == 3
    assert [item["hiring_job_label"] for item in queued_commands[0]["cohorts"]] == [
        "Customer Service Representative",
        "Sales Member",
    ]
    assert lark_calls == []

    second = bot._queue_talent_search_publication(task_id)
    assert second["status"] == "queued"
    assert second["idempotent"] is True
    assert len(queued_commands) == 1
    assert lark_calls == []

    worker_token = "publication-worker-token-with-at-least-32-characters"
    publication_payload = copy.deepcopy(queued_commands[0])
    bot.NEXUS_TALENT_WORKER_TOKEN = worker_token
    bot.db.claim_talent_daily_publication = lambda worker_id, lease_seconds: (
        {
            "publication_id": publication_payload["publication_id"],
            "payload": publication_payload,
        },
        "publication-route-lease-token-with-at-least-32-characters",
    )
    claimed = bot.app.test_client().post(
        "/api/integration/v1/talent/publications/claim",
        json={"worker_id": "windows-publication-test", "lease_seconds": 120},
        headers={"Authorization": f"Bearer {worker_token}"},
    )
    assert claimed.status_code == 200
    assert claimed.get_json()["task"] == publication_payload

    print("Manager approval queues a local-worker publication: PASSED")
    print("Publication claim returns the immutable payload: PASSED")
    print("Preview completion and Railway Lark writes: BLOCKED")


if __name__ == "__main__":
    main()
