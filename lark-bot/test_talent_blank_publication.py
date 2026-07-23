import copy
import datetime

import bot


def main():
    bot.PANEL_PASSWORD = "blank-publication-panel-password"
    bot._kolkata_today = lambda: datetime.date(2026, 7, 23)
    stored = {"row": None, "roster": None}
    bot.db.get_talent_daily_publication_by_date = (
        lambda _business_date: copy.deepcopy(stored["row"])
    )
    bot.db.operational_job_catalog = lambda statuses=("open",): [
        {
            "job_ref": "REQ-20260723-SALES",
            "title": "Sales Member",
            "status": "open",
        },
        {
            "job_ref": "REQ-20260723-CSR",
            "title": "Customer Service Representative",
            "status": "open",
        },
    ]
    bot.db.set_setting = lambda key, value: stored.update(roster=(key, value))

    def queue(publication_id, business_date, command, task_ids):
        assert task_ids == []
        assert command["schema_version"] == "talent-daily-publication-task-v4"
        assert command["cohorts"] == []
        assert command["total_contact_count"] == 0
        assert command["hr_names"] == ["JENNIFER", "SANDRINE"]
        assert command["manual_rows_per_hr"] == 30
        assert command["source_channels"] == list(bot.channel_report.CHANNELS)
        assert [item["hiring_job_label"] for item in command["open_jobs"]] == [
            "Sales Member",
            "Customer Service Representative",
        ]
        stored["row"] = {
            "publication_id": publication_id,
            "business_date": business_date,
            "status": "queued",
            "payload": copy.deepcopy(command),
            "receipt": {},
        }
        return copy.deepcopy(stored["row"])

    bot.db.queue_talent_daily_publication = queue
    client = bot.app.test_client()
    headers = {"X-Auth": bot.PANEL_PASSWORD}
    first = client.post(
        "/api/talent/publications/today",
        json={
            "hr_names": ["JENNIFER", "SANDRINE"],
            "manual_rows_per_hr": 30,
        },
        headers=headers,
    )
    assert first.status_code == 201
    assert first.get_json()["expected_rows"] == 0
    assert first.get_json()["requires_local_worker"] is True
    assert stored["roster"] == (
        "channel_roster",
        "JENNIFER\nSANDRINE",
    )

    repeated = client.post(
        "/api/talent/publications/today",
        json={"hr_names": ["JENNIFER", "SANDRINE"]},
        headers=headers,
    )
    assert repeated.status_code == 200
    assert repeated.get_json()["idempotent"] is True
    assert repeated.get_json()["publication_id"] == first.get_json()["publication_id"]

    stored["row"]["status"] = "failed"
    stored["row"]["receipt"] = {"error_code": "publication_failed"}
    failed = client.post(
        "/api/talent/publications/today",
        json={"hr_names": ["JENNIFER", "SANDRINE"]},
        headers=headers,
    )
    assert failed.status_code == 200
    assert failed.get_json()["status"] == "failed"
    assert failed.get_json()["can_reset"] is True
    assert failed.get_json()["requires_local_worker"] is False
    assert failed.get_json()["error_code"] == "publication_failed"

    print("Manager can create today's workbook without any search: PASSED")
    print("Open Job Requisitions and HR roster are signed into the task: PASSED")
    print("Repeated create/open request is idempotent: PASSED")
    print("Failed publication is reported for safe reset instead of false queueing: PASSED")


if __name__ == "__main__":
    main()
