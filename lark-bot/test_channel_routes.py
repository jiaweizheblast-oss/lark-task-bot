import datetime

import bot
import channel_pipeline_schema as pipeline_schema
import channel_report


PANEL_PASSWORD = "panel-password-for-channel-route-test"
WORKER_TOKEN = "worker-token-for-channel-route-test-at-least-32"
TODAY = datetime.date(2026, 7, 22)


def main():
    bot.PANEL_PASSWORD = PANEL_PASSWORD
    bot.NEXUS_TALENT_WORKER_TOKEN = WORKER_TOKEN
    bot._kolkata_today = lambda: TODAY
    settings = {
        "channel_roster": "Asha\nNeha",
        "channel_go_live": "2026-07-22",
    }
    open_job = {
        "id": 7, "job_ref": "REQ-007", "title": "Sales",
        "target_headcount": 20, "catalog_revision": 1, "status": "open",
    }
    closed_job = {
        "id": 8, "job_ref": "REQ-008", "title": "Closed Job",
        "target_headcount": 1, "catalog_revision": 1, "status": "closed",
    }
    bot.db.get_settings = lambda: dict(settings)
    bot.db.get_setting = lambda key: settings.get(key)
    bot.db.set_setting = lambda key, value: settings.__setitem__(key, value)
    bot.db.list_job_requests = lambda only_open=False: (
        [open_job] if only_open else [open_job, closed_job]
    )
    bot.db.list_lark_referenced_job_request_ids = lambda: []
    bot.db.candidate_data_days = lambda: []
    bot.db.channel_data_days = lambda: []
    bot.db.earliest_candidate_date = lambda: None

    ensure_calls = []

    def ensure_schema(app_token, table_id, *args, **kwargs):
        ensure_calls.append((app_token, table_id, args, kwargs))
        return {"ok": True, "verification": {"ok": True}}

    bot.lark_bitable.ensure_channel_base_schema = ensure_schema
    bot.lark_bitable.verify_channel_base_schema = ensure_schema

    ensured = bot._ensure_lark_channel_schema({
        "app_token": "app-token",
        "pipeline_table_id": "table-id",
        "last_sync": "",
    })
    assert ensured["ok"]
    assert len(ensure_calls) == 1
    app_token, table_id, positional, options = ensure_calls[0]
    assert (app_token, table_id, positional) == ("app-token", "table-id", ())
    assert options["job_titles"] == ["Sales"]
    assert options["hr_names"] == ["Asha", "Neha"]
    assert options["channels"] == channel_report.CHANNELS
    assert options["stages"] == channel_report.PIPELINE_STATUS

    client = bot.app.test_client()
    auth = {"X-Auth": PANEL_PASSWORD}
    worker_auth = {"Authorization": f"Bearer {WORKER_TOKEN}"}

    assert client.get("/api/channel/meta").status_code == 401
    meta = client.get("/api/channel/meta", headers=auth)
    assert meta.status_code == 200
    meta_json = meta.get_json()
    assert meta_json["jobs"] == [{
        "id": 7, "job_ref": "REQ-007", "title": "Sales",
        "target_headcount": 20, "catalog_revision": 1,
    }]
    assert meta_json["roster"] == ["Asha", "Neha"]
    assert meta_json["channels"] == channel_report.CHANNELS
    assert meta_json["statuses"] == channel_report.PIPELINE_STATUS
    assert meta_json["timezone"] == "Asia/Kolkata"

    assert client.get("/api/channel/template", headers=auth).status_code == 410
    assert client.post("/api/channel/upload", headers=auth).status_code == 410
    assert client.get("/api/channel/template").status_code == 401

    settings.update({
        "lark_channel_app_token": "app-token",
        "lark_channel_pipeline_table_id": "table-id",
        "lark_channel_url": "https://lark.example.test/base/app-token",
        "lark_channel_schema_version": pipeline_schema.SCHEMA_VERSION,
        "lark_channel_business_date": "2026-07-21",
        "lark_channel_last_sync": "",
    })
    assert client.get("/api/integration/v1/channel/status").status_code == 401
    old_status = client.get(
        "/api/integration/v1/channel/status", headers=worker_auth,
    ).get_json()
    assert old_status["configured"] is False
    assert old_status["today"] == "2026-07-22"
    assert client.post(
        "/api/integration/v1/channel/submit", headers=worker_auth,
    ).status_code == 409

    settings["lark_channel_business_date"] = "2026-07-22"
    settings["lark_channel_schema_ensured"] = ""
    sync_calls = []

    def sync_table(database, lark_client, config, **kwargs):
        sync_calls.append((database, lark_client, dict(config), dict(kwargs)))
        return {
            "ok": True, "status": "synchronized", "created": 2,
            "updated": 0, "errors": [],
        }

    bot.channel_sheet_service.sync_lark_table = sync_table
    bot._ensure_lark_channel_schema = lambda cfg=None: {"ok": True}
    current_status = client.get(
        "/api/integration/v1/channel/status", headers=worker_auth,
    ).get_json()
    assert current_status["configured"] is True
    assert current_status["schema_ensured"] is True

    submitted = client.post(
        "/api/integration/v1/channel/submit", headers=worker_auth,
    )
    assert submitted.status_code == 200
    assert submitted.get_json()["created"] == 2
    pulled = client.post("/api/lark/pull", headers=auth)
    assert pulled.status_code == 200
    assert len(sync_calls) == 2
    for _, _, config, kwargs in sync_calls:
        assert config["business_date"] == "2026-07-22"
        assert kwargs["default_date"] == "2026-07-22"
        assert kwargs["channels"] == channel_report.CHANNELS

    # With no table for the current day, creation uses one dated Base/table and
    # the same Open-job, source, status and HR catalogs used by the importer.
    for key in list(settings):
        if key.startswith("lark_channel_"):
            settings.pop(key)
    create_calls = []

    def create_base(name, job_titles, channels, stages, **kwargs):
        create_calls.append((name, job_titles, channels, stages, kwargs))
        return {
            "ok": True,
            "app_token": "new-app",
            "pipeline_table_id": "new-table",
            "url": "https://lark.example.test/base/new-app",
            "schema": {"ok": True},
        }

    bot.lark_bitable.create_channel_base = create_base
    created = client.post("/api/lark/table", json={}, headers=auth)
    assert created.status_code == 200
    assert created.get_json()["business_date"] == "2026-07-22"
    assert len(create_calls) == 1
    name, jobs, channels, stages, options = create_calls[0]
    assert name == "Recruiting20260722"
    assert jobs == ["Sales"]
    assert channels == channel_report.CHANNELS
    assert stages == channel_report.PIPELINE_STATUS
    assert options["hr_names"] == ["Asha", "Neha"]
    assert options["table_name"] == "Recruiting20260722"

    metric_calls = []
    bot.db.list_candidate_metric_rows_range = lambda dfrom, dto: metric_calls.append(
        (dfrom, dto)
    ) or [{
        "record_date": TODAY,
        "channel": "LinkedIn",
        "job_request_id": 7,
        "new_resumes": 1,
        "passed_screening": 0,
        "recommended": 0,
        "rejected": 0,
    }]
    bot.db.list_candidate_application_snapshot = lambda: [{
        "job_request_id": 7,
        "job_title": "Sales",
        "status": "HR Screening",
        "channel": "LinkedIn",
        "filled_by": "Asha",
        "baseline_import": True,
    }]
    bot.db.list_channel_costs = lambda: []
    analytics = client.get(
        "/api/channel/analytics?from=2026-07-22&to=2026-07-22",
        headers=auth,
    )
    assert analytics.status_code == 200
    analytics_json = analytics.get_json()
    assert analytics_json["space"] == "identity_derived"
    assert analytics_json["summary"]["new"] == 1
    assert analytics_json["snapshot"]["total"] == 1
    assert analytics_json["snapshot"]["baseline_count"] == 1
    assert metric_calls
    legacy_bookmark = client.get(
        "/api/channel/analytics?space=manual&from=2026-07-22&to=2026-07-22",
        headers=auth,
    ).get_json()
    assert legacy_bookmark["space"] == "identity_derived"
    assert legacy_bookmark["summary"]["new"] == 1

    print("Single-table Channel Analytics routes: PASSED")
    print("Excel retired; current-day Bot/website submit guard: PASSED")
    print("Dated table, Open jobs and HR/source/status catalogs: PASSED")
    print("Manager analytics defaults to immutable candidate events: PASSED")


if __name__ == "__main__":
    main()
