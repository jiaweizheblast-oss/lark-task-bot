import lark_bitable
import sheet_io
import channel_pipeline_schema as pipeline_schema


def main():
    original_token = lark_bitable.tenant_token
    original_req = lark_bitable._req
    calls = []

    def fake_req(method, path, token=None, body=None, timeout=15):
        calls.append((method, path))
        if method == "GET" and path.endswith("/tables?page_size=100"):
            return {"code": 0, "data": {"items": [
                {"table_id": "pipeline", "name": "Candidate Pipeline"},
                {"table_id": "manual", "name": "未建档批量统计（特殊情况）"},
                {"table_id": "empty-default", "name": "Table"},
                {"table_id": "used-default", "name": "数据表"},
                {"table_id": "other-empty", "name": "HR Notes"},
            ]}}
        if method == "GET" and "empty-default/records" in path:
            return {"code": 0, "data": {"items": [
                {"record_id": "blank-lark-row", "fields": {}},
            ]}}
        if method == "GET" and "used-default/records" in path:
            return {"code": 0, "data": {"items": [
                {"record_id": "keep-me", "fields": {"Text": "real value"}},
            ]}}
        if method == "DELETE" and path.endswith("/tables/empty-default"):
            return {"code": 0, "msg": "success"}
        raise AssertionError((method, path))

    try:
        lark_bitable.tenant_token = lambda: ("fixture-token", None)
        lark_bitable._req = fake_req
        result = lark_bitable.cleanup_empty_default_tables(
            "app-token", protected_table_ids=("pipeline", "manual"))
    finally:
        lark_bitable.tenant_token = original_token
        lark_bitable._req = original_req

    assert result["ok"] is True
    assert result["removed"] == [{"table_id": "empty-default", "name": "Table"}]
    assert result["retained"] == [
        {"table_id": "used-default", "name": "数据表", "reason": "not_empty"}
    ]
    assert not any("pipeline/records" in path or "manual/records" in path for _, path in calls)
    assert not any("other-empty" in path for _, path in calls)
    assert [call for call in calls if call[0] == "DELETE"] == [
        ("DELETE", "/open-apis/bitable/v1/apps/app-token/tables/empty-default")
    ]

    migration_calls = []

    def fake_manual_migration_req(method, path, token=None, body=None, timeout=15):
        migration_calls.append((method, path, body))
        if method == "GET" and path.endswith("/tables?page_size=100"):
            return {"code": 0, "data": {"items": [
                {"table_id": "manual-old", "name": "未建档批量统计（特殊情况）"},
            ]}}
        if method == "GET" and "manual-old/records?page_size=500" in path:
            return {"code": 0, "data": {"items": [
                {"record_id": "blank", "fields": {}},
            ]}}
        if method == "POST" and path.endswith("/tables"):
            assert body["table"]["name"] == pipeline_schema.MANUAL_TABLE_NAME
            assert body["table"]["default_view_name"] == pipeline_schema.MANUAL_VIEW_NAME
            return {"code": 0, "data": {"table_id": "manual-english"}}
        raise AssertionError((method, path, body))

    try:
        lark_bitable.tenant_token = lambda: ("fixture-token", None)
        lark_bitable._req = fake_manual_migration_req
        manual_migration = lark_bitable.prepare_canonical_manual_table(
            "app-token", "manual-old", ["Sales"], ["LinkedIn", "Other"])
    finally:
        lark_bitable.tenant_token = original_token
        lark_bitable._req = original_req

    assert manual_migration == {
        "ok": True, "changed": True, "table_id": "manual-english",
        "old_table_id": "manual-old",
    }

    choice_calls = []
    channel_spec = next(
        spec for spec in pipeline_schema.PIPELINE_COLUMNS if spec["key"] == "channel")

    def fake_choice_req(method, path, token=None, body=None, timeout=15):
        choice_calls.append((method, path, body))
        if method == "GET" and path.endswith("/fields?page_size=100"):
            return {"code": 0, "data": {"items": [{
                "field_id": "fld-channel", "field_name": "Source Channel", "type": 3,
                "property": {"options": [{"name": "LinkedIn"}, {"name": "Shine"}]},
            }]}}
        if method == "PUT" and path.endswith("/fields/fld-channel"):
            assert body["property"]["options"] == [
                {"name": "LinkedIn"}, {"name": "Other"}]
            return {"code": 0, "msg": "success"}
        raise AssertionError((method, path, body))

    try:
        lark_bitable.tenant_token = lambda: ("fixture-token", None)
        lark_bitable._req = fake_choice_req
        choices = lark_bitable.synchronize_choice_field_options(
            "app-token", "pipeline", (channel_spec,),
            channels=("LinkedIn", "Other"))
    finally:
        lark_bitable.tenant_token = original_token
        lark_bitable._req = original_req

    assert choices == {"ok": True, "updated": ["Source Channel"]}

    rename_calls = []
    renamed = {"value": False}
    source_detail_spec = next(
        spec for spec in pipeline_schema.PIPELINE_COLUMNS
        if spec["key"] == "source_detail"
    )

    def fake_rename_req(method, path, token=None, body=None, timeout=15):
        rename_calls.append((method, path, body))
        if method == "GET" and path.endswith("/fields?page_size=100"):
            return {"code": 0, "data": {"items": [{
                "field_id": "fld-source-detail",
                "field_name": "其他来源说明（选择 Other 时填写）",
                "type": 1,
                "property": {},
                "ui_type": "Text",
            }]}}
        if method == "PUT" and path.endswith("/fields/fld-source-detail"):
            assert body == {
                "field_name": pipeline_schema.OTHER_SOURCE_DETAIL,
                "type": 1,
            }
            renamed["value"] = True
            return {"code": 0, "msg": "success"}
        raise AssertionError((method, path, body))

    try:
        lark_bitable.tenant_token = lambda: ("fixture-token", None)
        lark_bitable._req = fake_rename_req
        rename_result = lark_bitable.normalize_table_field_names(
            "app-token", "pipeline", (source_detail_spec,))
    finally:
        lark_bitable.tenant_token = original_token
        lark_bitable._req = original_req

    assert renamed["value"] is True and rename_result["ok"] is True
    assert "description" not in rename_calls[-1][2]
    assert "property" not in rename_calls[-1][2]
    assert "ui_type" not in rename_calls[-1][2]

    canonical_lark_headers = [
        spec["header"] for spec in pipeline_schema.columns_for("lark")
    ] + [spec["header"] for spec in pipeline_schema.MANUAL_COLUMNS]
    assert all(header.isascii() for header in canonical_lark_headers)
    assert "Required if Source Channel is Other" in pipeline_schema.OTHER_SOURCE_DETAIL

    assert lark_bitable.table_url(
        "https://example.test/base/app?table=default&view=old", "pipeline"
    ) == "https://example.test/base/app?table=pipeline"

    field_calls = []
    stage_removed = {"value": False}

    def fake_field_req(method, path, token=None, body=None, timeout=15):
        field_calls.append((method, path, body))
        if method == "GET" and path.endswith("/fields?page_size=100"):
            if stage_removed["value"]:
                return {"code": 0, "data": {"items": [
                    {"field_id": "fld-stage", "field_name": "Current Stage", "type": 3},
                ]}}
            return {"code": 0, "data": {"items": [
                {"field_id": "fld-stage", "field_name": "Current Stage", "type": 3},
                {"field_id": "fld-date", "field_name": "Stage Date", "type": 1},
            ]}}
        if method == "DELETE" and path.endswith("/fields/fld-date"):
            stage_removed["value"] = True
            return {"code": 0, "msg": "success"}
        raise AssertionError((method, path, body))

    try:
        lark_bitable.tenant_token = lambda: ("fixture-token", None)
        lark_bitable._req = fake_field_req
        stage_date = lark_bitable.remove_legacy_lark_stage_date_field("app-token", "pipeline")
    finally:
        lark_bitable.tenant_token = original_token
        lark_bitable._req = original_req

    assert stage_date["ok"] is True and stage_date["removed"] is True
    assert [call[:2] for call in field_calls if call[0] == "DELETE"] == [
        ("DELETE", "/open-apis/bitable/v1/apps/app-token/tables/pipeline/fields/fld-date")
    ]

    entry_calls = []
    entry_state = {"old_deleted": False, "temp_deleted": False}

    def fake_entry_req(method, path, token=None, body=None, timeout=15):
        entry_calls.append((method, path, body))
        if method == "GET" and path.endswith("/fields?page_size=100"):
            items = [{"field_id": "fld-candidate", "field_name": "Candidate", "type": 1}]
            if not entry_state["old_deleted"]:
                items.append({"field_id": "fld-entry-old", "field_name": "Entry Date",
                              "type": 1})
            if not entry_state["temp_deleted"]:
                items.append({"field_id": "fld-entry-temp",
                              "field_name": "Entry Date (legacy migration)", "type": 1})
            return {"code": 0, "data": {"items": items}}
        if method == "DELETE" and path.endswith("/fields/fld-entry-old"):
            entry_state["old_deleted"] = True
            return {"code": 0, "msg": "success"}
        if method == "DELETE" and path.endswith("/fields/fld-entry-temp"):
            entry_state["temp_deleted"] = True
            return {"code": 0, "msg": "success"}
        raise AssertionError((method, path, body))

    try:
        lark_bitable.tenant_token = lambda: ("fixture-token", None)
        lark_bitable._req = fake_entry_req
        entry_date = lark_bitable.remove_legacy_lark_entry_date_field(
            "app-token", "pipeline")
        date_verification = lark_bitable._verify_system_date_fields(
            "app-token", "pipeline")
    finally:
        lark_bitable.tenant_token = original_token
        lark_bitable._req = original_req

    assert entry_date == {"ok": True,
                          "removed": ["fld-entry-old", "fld-entry-temp"]}
    assert date_verification["ok"] is True
    assert not any(method in {"PUT", "POST"} for method, path, body in entry_calls)

    system_id_calls = []
    system_id_state = {"deleted": False}

    def fake_system_id_req(method, path, token=None, body=None, timeout=15):
        system_id_calls.append((method, path, body))
        if method == "GET" and path.endswith("/fields?page_size=100"):
            items = [{"field_id": "fld-candidate", "field_name": "Candidate", "type": 1}]
            if not system_id_state["deleted"]:
                items.append({"field_id": "fld-system-id", "field_name": "System ID",
                              "type": 1})
            return {"code": 0, "data": {"items": items}}
        if method == "DELETE" and path.endswith("/fields/fld-system-id"):
            system_id_state["deleted"] = True
            return {"code": 0, "msg": "success"}
        raise AssertionError((method, path, body))

    try:
        lark_bitable.tenant_token = lambda: ("fixture-token", None)
        lark_bitable._req = fake_system_id_req
        system_id = lark_bitable.remove_lark_system_id_field(
            "app-token", "pipeline")
        internal_verification = lark_bitable._verify_system_date_fields(
            "app-token", "pipeline")
    finally:
        lark_bitable.tenant_token = original_token
        lark_bitable._req = original_req

    assert system_id["ok"] is True and system_id["removed"] is True
    assert internal_verification["ok"] is True

    specs = lark_bitable._pipeline_fields_spec(
        ["Sales"], ["LinkedIn", "Other"], ["New Lead"])
    headers = [field["field_name"] for field in specs]
    assert headers.index(pipeline_schema.OTHER_SOURCE_DETAIL) == headers.index("Source Channel") + 1
    assert headers[:8] == [
        "Candidate", "Source Channel",
        pipeline_schema.OTHER_SOURCE_DETAIL, "Job", "Current Stage",
        "HR Owner", "Rejection Reason", "Note",
    ]
    xlsx_headers = [column["header"] for column in sheet_io.pipeline_columns(["Sales"])]
    assert "Entry Date" not in xlsx_headers
    assert "Stage Started On" not in headers
    assert "Stage Started On" not in xlsx_headers
    assert [header for header in xlsx_headers
            if header not in {"System ID", "Row Ref", "System Row Token"}] == headers
    by_name = {field["field_name"]: field for field in specs}
    assert "Entry Date" not in by_name
    assert "System ID" not in by_name

    cleanup_calls = []

    def fake_test_cleanup_req(method, path, token=None, body=None, timeout=15):
        cleanup_calls.append((method, path, body))
        if method == "GET" and path.endswith("/records?page_size=500"):
            return {"code": 0, "data": {"items": [
                {"record_id": "rec-test", "fields": {
                    "Candidate": "hhb", "Source Channel": "Talent Discovery",
                    pipeline_schema.OTHER_SOURCE_DETAIL: "dvvzvz", "System ID": "",
                }},
                {"record_id": "rec-protected", "fields": {
                    "Candidate": "hhb", "Source Channel": "Talent Discovery",
                    pipeline_schema.OTHER_SOURCE_DETAIL: "dvvzvz", "System ID": "17",
                }},
                {"record_id": "rec-real", "fields": {
                    "Candidate": "Real Candidate", "Source Channel": "LinkedIn",
                    pipeline_schema.OTHER_SOURCE_DETAIL: "", "System ID": "",
                }},
            ]}}
        if method == "DELETE" and path.endswith("/records/rec-test"):
            return {"code": 0, "msg": "success"}
        raise AssertionError((method, path, body))

    try:
        lark_bitable.tenant_token = lambda: ("fixture-token", None)
        lark_bitable._req = fake_test_cleanup_req
        test_cleanup = lark_bitable.delete_known_unsynced_test_rows("app-token", "pipeline")
    finally:
        lark_bitable.tenant_token = original_token
        lark_bitable._req = original_req

    assert test_cleanup == {"ok": True, "removed": ["rec-test"]}
    assert not any("rec-protected" in path or "rec-real" in path
                   for method, path, body in cleanup_calls if method == "DELETE")

    print("Empty Lark default table cleanup is bounded and safe: PASSED")
    print("Candidate Pipeline direct-link canonicalisation: PASSED")
    print("Workflow dates are service-owned and absent from the Lark HR surface: PASSED")


if __name__ == "__main__":
    main()
