import copy
import datetime
import uuid

import bot


def main():
    bot.PANEL_PASSWORD = "replacement-panel-password"
    bot._talent_worker_status = lambda _capability=None: {"online": True}
    bot._kolkata_today = lambda: datetime.date(2026, 7, 23)
    original = bot.talent_search_queue.build_publication_task(
        [],
        publication_id=str(uuid.uuid4()),
        business_date="2026-07-23",
        hr_names=["JENNIFER", "SANDRINE"],
        open_jobs=[
            {
                "operational_job_ref": "REQ-20260723-SALES",
                "hiring_job_label": "Sales Member",
            }
        ],
        source_channels=["LinkedIn", "Other"],
        manual_rows_per_hr=500,
        now=datetime.datetime(2026, 7, 23, 12, tzinfo=datetime.timezone.utc),
    )
    stored = {
        "publication_id": original["publication_id"],
        "business_date": datetime.date(2026, 7, 23),
        "revision": 1,
        "status": "published",
        "payload": copy.deepcopy(original),
        "payload_sha256": original["payload_sha256"],
        "receipt": {"spreadsheet_url": "https://example.test/old"},
    }
    captured = {}
    bot.db.get_talent_daily_publication = lambda _publication_id: copy.deepcopy(stored)

    def replace(publication_id, expected_sha, replacement):
        assert publication_id == original["publication_id"]
        assert expected_sha == original["payload_sha256"]
        captured["replacement"] = copy.deepcopy(replacement)
        return {
            "status": "queued",
            "publication_id": replacement["publication_id"],
        }

    bot.db.replace_published_talent_daily_publication = replace
    client = bot.app.test_client()
    headers = {"X-Auth": bot.PANEL_PASSWORD}

    assert client.post(
        f"/api/talent/publications/{original['publication_id']}/replace-unused",
        json={"confirm": "REPLACE_UNUSED_PUBLISHED"},
    ).status_code == 401
    assert client.post(
        f"/api/talent/publications/{original['publication_id']}/replace-unused",
        json={"confirm": "wrong"},
        headers=headers,
    ).status_code == 422
    response = client.post(
        f"/api/talent/publications/{original['publication_id']}/replace-unused",
        json={"confirm": "REPLACE_UNUSED_PUBLISHED"},
        headers=headers,
    )
    assert response.status_code == 201
    body = response.get_json()
    assert body["revision"] == 2
    assert body["old_lark_workbook_retained"] is True
    replacement = captured["replacement"]
    assert replacement["revision"] == 2
    assert replacement["business_date"] == original["business_date"]
    assert replacement["hr_names"] == original["hr_names"]
    assert replacement["open_jobs"] == original["open_jobs"]
    assert replacement["source_channels"] == original["source_channels"]
    assert replacement["manual_rows_per_hr"] == 500
    assert replacement["cohorts"] == []
    assert replacement["payload_sha256"] == bot.talent_search_queue.sha256({
        key: value
        for key, value in replacement.items()
        if key != "payload_sha256"
    })

    print("Published unused workbook replacement requires exact confirmation: PASSED")
    print("Revision 2 retains HR, job, source, and frozen cohort scope: PASSED")
    print("Old Lark workbook is retained for audit: PASSED")


if __name__ == "__main__":
    main()
