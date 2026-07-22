from pathlib import Path

import bot
import db
import lark_bitable


def test_lark_batched_reset():
    original_token = lark_bitable.tenant_token
    original_req = lark_bitable._req
    calls = []

    def fake_req(method, path, token=None, body=None, timeout=15):
        calls.append((method, path, body))
        if method == "GET" and "page_token=" not in path:
            return {"code": 0, "data": {
                "items": [{"record_id": "rec-%03d" % i} for i in range(500)],
                "has_more": True, "page_token": "next",
            }}
        if method == "GET" and "page_token=next" in path:
            return {"code": 0, "data": {
                "items": [{"record_id": "rec-500"}], "has_more": False,
            }}
        if method == "POST" and path.endswith("/records/batch_delete"):
            return {"code": 0, "msg": "success"}
        raise AssertionError((method, path, body))

    try:
        lark_bitable.tenant_token = lambda: ("fixture-token", None)
        lark_bitable._req = fake_req
        result = lark_bitable.delete_all_table_records("app", "pipeline")
    finally:
        lark_bitable.tenant_token = original_token
        lark_bitable._req = original_req

    assert result == {"ok": True, "removed": 501}
    batches = [body["records"] for method, path, body in calls
               if method == "POST"]
    assert [len(batch) for batch in batches] == [500, 1]


def test_admin_reset_route():
    original_cfg = bot._lark_cfg
    original_lark_reset = bot.lark_bitable.delete_all_table_records
    original_db_reset = bot.db.reset_channel_analytics_test_data
    bot.PANEL_PASSWORD = "reset-test-password"
    cfg = {
        "app_token": "app", "pipeline_table_id": "pipeline",
        "url": "https://example.test/base",
        "last_sync": "fixture", "schema_version": "recruiting-daily-v20",
    }
    calls = []
    bot._lark_cfg = lambda: cfg
    bot.lark_bitable.delete_all_table_records = lambda app, table: (
        calls.append(("lark", app, table)) or
        {"ok": True, "removed": 4 if table == "pipeline" else 2}
    )
    bot.db.reset_channel_analytics_test_data = lambda: (
        calls.append(("database",)) or {
            "applications": 4, "application_stage_events": 6,
            "legacy_stage_events": 0, "submission_events": 4,
            "candidates": 4, "manual_batch_counts": 2,
        }
    )
    client = bot.app.test_client()
    rejected = client.post(
        "/api/channel/reset-test-data",
        headers={"X-Auth": bot.PANEL_PASSWORD},
        json={"confirmation": "RESET"},
    )
    assert rejected.status_code == 422 and calls == []

    reset = client.post(
        "/api/channel/reset-test-data",
        headers={"X-Auth": bot.PANEL_PASSWORD},
        json={"confirmation": "RESET CHANNEL ANALYTICS TEST DATA"},
    )
    assert reset.status_code == 200
    result = reset.get_json()
    assert result["ok"] is True
    assert result["lark_pipeline_rows"] == 4
    assert result["jobs_preserved"] is True
    assert calls == [
        ("lark", "app", "pipeline"),
        ("database",),
    ]
    bot._lark_cfg = original_cfg
    bot.lark_bitable.delete_all_table_records = original_lark_reset
    bot.db.reset_channel_analytics_test_data = original_db_reset

    panel = (Path(__file__).resolve().parent / "panel.html").read_text(encoding="utf-8")
    assert "Reset Test Data" in panel
    assert "RESET CHANNEL ANALYTICS TEST DATA" in panel
    assert "Talent Discovery are preserved" in panel
    assert "Type RESET" not in panel


class _FakeCursor:
    def __init__(self, fail_on=""):
        self.calls = []
        self.rowcount = 0
        self.fail_on = fail_on

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, statement, params=None):
        compact = " ".join(statement.split())
        self.calls.append((compact, params))
        if self.fail_on and self.fail_on in compact:
            raise RuntimeError("fixture database failure")
        self.rowcount = 1 if compact.startswith("DELETE") else 0


class _FakeConnection:
    def __init__(self, fail_on=""):
        self.autocommit = True
        self.cursor_fixture = _FakeCursor(fail_on=fail_on)
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return self.cursor_fixture

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_database_reset_transaction_and_scope():
    original_get_conn = db.get_conn
    connection = _FakeConnection()
    try:
        db.get_conn = lambda: connection
        counts = db.reset_channel_analytics_test_data()
    finally:
        db.get_conn = original_get_conn

    assert connection.autocommit is False
    assert connection.committed is True
    assert connection.rolled_back is False
    assert connection.closed is True
    assert counts == {
        "application_stage_events": 1,
        "legacy_stage_events": 1,
        "submission_events": 1,
        "applications": 1,
        "candidates": 1,
        "manual_batch_counts": 1,
    }
    statements = [statement for statement, _ in connection.cursor_fixture.calls]
    assert statements[:6] == [
        "DELETE FROM candidate_application_stage_event",
        "DELETE FROM candidate_stage_event",
        "DELETE FROM channel_submission_event",
        "DELETE FROM candidate_application",
        "DELETE FROM candidate",
        "DELETE FROM channel_daily",
    ]
    combined = " ".join(statements).lower()
    assert "delete from job_requests" not in combined
    assert "delete from talent_" not in combined


def test_database_reset_rolls_back_on_failure():
    original_get_conn = db.get_conn
    connection = _FakeConnection(fail_on="DELETE FROM candidate_application")
    try:
        db.get_conn = lambda: connection
        try:
            db.reset_channel_analytics_test_data()
            raise AssertionError("reset should have failed")
        except RuntimeError as exc:
            assert "fixture database failure" in str(exc)
    finally:
        db.get_conn = original_get_conn

    assert connection.committed is False
    assert connection.rolled_back is True
    assert connection.closed is True


def main():
    test_lark_batched_reset()
    test_admin_reset_route()
    test_database_reset_transaction_and_scope()
    test_database_reset_rolls_back_on_failure()
    print("Admin-only Channel Analytics test reset: PASSED")


if __name__ == "__main__":
    main()
