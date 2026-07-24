import copy
import datetime
import json
import uuid

import bot


PANEL_PASSWORD = "reconfiguration-panel-password"


def main():
    bot.PANEL_PASSWORD = PANEL_PASSWORD
    bot._kolkata_today = lambda: datetime.date(2026, 7, 24)
    bot._talent_worker_status = lambda _capability=None: {
        "online": True,
        "search_ready": True,
    }
    original = bot.talent_search_queue.build_publication_task(
        [],
        publication_id=str(uuid.uuid4()),
        business_date="2026-07-24",
        hr_names=["JENNIFER", "SANDRINE"],
        open_jobs=[
            {
                "operational_job_ref": "REQ-SALES",
                "hiring_job_label": "Sales Member",
            },
        ],
        source_channels=["LinkedIn", "Other"],
        manual_rows_per_hr=500,
        now=datetime.datetime(2026, 7, 24, 1, tzinfo=datetime.timezone.utc),
    )
    current = {
        "publication_id": original["publication_id"],
        "business_date": datetime.date(2026, 7, 24),
        "revision": 1,
        "status": "published",
        "payload": copy.deepcopy(original),
        "payload_sha256": original["payload_sha256"],
        "receipt": {"spreadsheet_url": "https://example.test/current"},
    }
    profiles = {
        "profile-sales": {"core_job_ref": "profile-sales", "status": "open"},
    }
    jobs = {
        "REQ-SALES": {
            "job_ref": "REQ-SALES",
            "record_type": "operational",
            "status": "open",
            "search_profile_ref": "profile-sales",
        },
    }
    bot.db.get_talent_daily_publication = (
        lambda publication_id: (
            copy.deepcopy(current)
            if publication_id == original["publication_id"]
            else None
        )
    )
    bot.db.get_talent_daily_publication_by_date = (
        lambda _business_date: copy.deepcopy(current)
    )
    bot.db.get_job_request_by_core_ref = profiles.get
    bot.db.get_job_request_by_ref = jobs.get
    bot.db.list_talent_search_tasks = lambda limit=500: []
    settings = {}
    bot.db.set_setting = settings.__setitem__
    captured = {}

    def enqueue(tasks, search_run_id):
        captured["tasks"] = copy.deepcopy(tasks)
        captured["search_run_id"] = search_run_id
        return [
            {
                **copy.deepcopy(task),
                "payload": copy.deepcopy(task),
                "status": "pending",
                "search_run_id": search_run_id,
                "search_run_order": index,
                "search_run_size": len(tasks),
                "created_at": datetime.datetime.fromisoformat(task["created_at"]),
                "updated_at": datetime.datetime.now(datetime.timezone.utc),
                "expires_at": datetime.datetime.fromisoformat(task["expires_at"]),
            }
            for index, task in enumerate(tasks, start=1)
        ], len(tasks)

    bot.db.enqueue_talent_search_batch = enqueue
    client = bot.app.test_client()
    response = client.post(
        "/api/talent/search-batches",
        headers={"X-Auth": PANEL_PASSWORD},
        json={
            "jobs": [
                {
                    "operational_job_ref": "REQ-SALES",
                    "core_job_ref": "profile-sales",
                },
            ],
            "hr_allocations": [
                {"name": "JENNIFER", "count": 15},
                {"name": "SANDRINE", "count": 15},
            ],
            "replace_publication_id": original["publication_id"],
            "confirm": "RECONFIGURE_UNUSED_PUBLISHED",
        },
    )
    assert response.status_code == 201, response.get_json()
    body = response.get_json()
    assert body["reconfiguration"] is True
    assert body["old_table_remains_available"] is True
    assert body["requested_contact_count"] == 30
    assert len(captured["tasks"]) == 1
    context = json.loads(
        settings["talent_reconfiguration_run:" + body["search_run_id"]]
    )
    assert context["publication_id"] == original["publication_id"]
    assert context["payload_sha256"] == original["payload_sha256"]
    assert context["revision"] == 1
    assert context["hr_allocations"] == [
        {"name": "JENNIFER", "count": 15},
        {"name": "SANDRINE", "count": 15},
    ]

    invalid = client.post(
        "/api/talent/search-batches",
        headers={"X-Auth": PANEL_PASSWORD},
        json={
            "jobs": [
                {
                    "operational_job_ref": "REQ-SALES",
                    "core_job_ref": "profile-sales",
                },
            ],
            "hr_allocations": [{"name": "JENNIFER", "count": 30}],
            "replace_publication_id": original["publication_id"],
            "confirm": "wrong",
        },
    )
    assert invalid.status_code == 422

    revision_two = bot.talent_search_queue.build_publication_task(
        [
            {
                "search_task_id": str(uuid.uuid4()),
                "operational_job_ref": "REQ-SALES",
                "core_job_ref": "profile-sales",
                "requested_contact_count": 1,
                "hr_allocations": [{"name": "JENNIFER", "count": 1}],
                "frozen_plan_sha256": "a" * 64,
                "frozen_plan_expires_at": "2026-07-24T12:00:00+00:00",
                "frozen_plan": {"candidates": [{"candidate_ref": "candidate-1"}]},
                "hiring_job_label": "Sales Member",
            },
        ],
        publication_id=str(uuid.uuid4()),
        business_date="2026-07-24",
        hr_names=["JENNIFER"],
        open_jobs=[
            {
                "operational_job_ref": "REQ-SALES",
                "hiring_job_label": "Sales Member",
            },
        ],
        source_channels=["LinkedIn", "Other"],
        manual_rows_per_hr=500,
        revision=2,
        apply_frozen_cohorts=True,
        now=datetime.datetime(2026, 7, 24, 2, tzinfo=datetime.timezone.utc),
    )
    assert revision_two["revision"] == 2
    assert revision_two["apply_frozen_cohorts"] is True

    print("Published workbook remains current while re-search is queued: PASSED")
    print("Reconfiguration context binds old publication and new allocation: PASSED")
    print("Revision 2 explicitly applies its newly frozen cohort: PASSED")


if __name__ == "__main__":
    main()
