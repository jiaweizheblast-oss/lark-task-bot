import copy
import datetime
import uuid

import bot


def main():
    bot.PANEL_PASSWORD = "repair-panel-password"
    bot._talent_worker_status = lambda _capability=None: {"online": True}
    bot._kolkata_today = lambda: datetime.date(2026, 7, 24)
    original = bot.talent_search_queue.build_publication_task(
        [],
        publication_id=str(uuid.uuid4()),
        business_date="2026-07-24",
        hr_names=["JENNIFER", "SANDRINE"],
        open_jobs=[
            {
                "operational_job_ref": "REQ-20260724-SALES",
                "hiring_job_label": "Sales Member",
            }
        ],
        source_channels=["LinkedIn", "Other"],
        manual_rows_per_hr=500,
        now=datetime.datetime(2026, 7, 24, 8, tzinfo=datetime.timezone.utc),
    )
    stored = {
        "publication_id": original["publication_id"],
        "business_date": datetime.date(2026, 7, 24),
        "revision": 1,
        "status": "failed",
        "payload": copy.deepcopy(original),
        "payload_sha256": original["payload_sha256"],
        "receipt": {},
        "last_error_code": "publication_failed",
    }
    bot.db.get_talent_daily_publication = lambda _publication_id: copy.deepcopy(
        stored
    )

    captured = {}

    def retry(publication_id, expected_sha):
        assert publication_id == original["publication_id"]
        assert expected_sha == original["payload_sha256"]
        captured["retried"] = publication_id
        return {
            "status": "queued",
            "publication_id": publication_id,
        }

    bot.db.retry_failed_talent_daily_publication = retry
    client = bot.app.test_client()
    headers = {"X-Auth": bot.PANEL_PASSWORD}

    assert client.post(
        f"/api/talent/publications/{original['publication_id']}/repair",
        json={"confirm": "REPAIR_FAILED_PUBLICATION"},
    ).status_code == 401
    assert client.post(
        f"/api/talent/publications/{original['publication_id']}/repair",
        json={"confirm": "wrong"},
        headers=headers,
    ).status_code == 422
    response = client.post(
        f"/api/talent/publications/{original['publication_id']}/repair",
        json={"confirm": "REPAIR_FAILED_PUBLICATION"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["revision"] == 1
    assert body["publication_id"] == original["publication_id"]
    assert body["scanner_calls"] == 0
    assert body["frozen_cohort_preserved"] is True
    assert body["existing_lark_workbook_reused"] is True
    assert captured["retried"] == original["publication_id"]

    status = bot._publication_status_json(stored)
    assert status["can_repair"] is True
    assert status["error_code"] == "publication_failed"
    assert status["hr_count"] == 2
    assert status["open_job_count"] == 1

    print("Failed publication repair retries the exact immutable task: PASSED")
    print("Repair reuses the Lark artifact and performs zero searches: PASSED")
    print("Today status exposes repair action and batch summary: PASSED")


if __name__ == "__main__":
    main()
