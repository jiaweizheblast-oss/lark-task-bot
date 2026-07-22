import lark_bitable
import channel_pipeline_schema as pipeline_schema


def main():
    original_token = lark_bitable.tenant_token
    original_req = lark_bitable._req
    original_ensure = lark_bitable.ensure_channel_base_schema

    # Lark creates a blank default table with every Base. Remove only that
    # disposable table; never inspect or delete the protected recruiting table,
    # a populated default table, or an unrelated business table.
    calls = []

    def fake_cleanup_req(method, path, token=None, body=None, timeout=15):
        del token, body, timeout
        calls.append((method, path))
        if method == "GET" and path.endswith("/tables?page_size=100"):
            return {"code": 0, "data": {"items": [
                {"table_id": "recruiting", "name": "Recruiting20260722"},
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
        lark_bitable._req = fake_cleanup_req
        result = lark_bitable.cleanup_empty_default_tables(
            "app-token", protected_table_ids=("recruiting",))
    finally:
        lark_bitable.tenant_token = original_token
        lark_bitable._req = original_req

    assert result["ok"] is True
    assert result["removed"] == [
        {"table_id": "empty-default", "name": "Table"}
    ]
    assert result["retained"] == [
        {"table_id": "used-default", "name": "数据表", "reason": "not_empty"}
    ]
    assert not any("recruiting/records" in path for _, path in calls)
    assert not any("other-empty" in path for _, path in calls)

    # A legacy Chinese label is migrated to the current unambiguous English
    # label without replacing the field or touching its data.
    rename_calls = []

    def fake_rename_req(method, path, token=None, body=None, timeout=15):
        del token, timeout
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
            return {"code": 0, "msg": "success"}
        raise AssertionError((method, path, body))

    source_detail_spec = next(
        spec for spec in pipeline_schema.PIPELINE_COLUMNS
        if spec["key"] == "source_detail"
    )
    try:
        lark_bitable.tenant_token = lambda: ("fixture-token", None)
        lark_bitable._req = fake_rename_req
        rename_result = lark_bitable.normalize_table_field_names(
            "app-token", "recruiting", (source_detail_spec,))
    finally:
        lark_bitable.tenant_token = original_token
        lark_bitable._req = original_req

    assert rename_result == {"ok": True, "updated": [{
        "from": "其他来源说明（选择 Other 时填写）",
        "to": pipeline_schema.OTHER_SOURCE_DETAIL,
    }]}
    assert "description" not in rename_calls[-1][2]
    assert "property" not in rename_calls[-1][2]
    assert "ui_type" not in rename_calls[-1][2]

    # The v20 surface is exactly one daily table with nine formal English
    # fields. Date is system-owned; jobs, HR allocation, sources and statuses
    # are controlled dropdowns.
    fields = lark_bitable._pipeline_fields_spec(
        ["Customer Service Representative"],
        ["LinkedIn", "Other"],
        ["Contacted / Awaiting Reply", "HR Screening", "Interview", "Offer",
         "Hired", "Rejected", "Withdrawn", "Resigned"],
        ["HR One", "HR Two"],
    )
    assert [field["field_name"] for field in fields] == [
        "Date", "Candidate Name", "Candidate URL", "Source Channel",
        pipeline_schema.OTHER_SOURCE_DETAIL, "Hiring Job", "Assigned HR",
        "Status", "CV",
    ]
    by_name = {field["field_name"]: field for field in fields}
    assert by_name["Date"]["type"] == lark_bitable.FT_CREATED_TIME
    assert by_name["Hiring Job"]["property"]["options"] == [
        {"name": "Customer Service Representative"}
    ]
    assert by_name["Assigned HR"]["property"]["options"] == [
        {"name": "HR One"}, {"name": "HR Two"}
    ]
    assert by_name["Source Channel"]["property"]["options"] == [
        {"name": "LinkedIn"}, {"name": "Other"}
    ]
    assert pipeline_schema.MANUAL_COLUMNS == ()

    # Creating today's workspace issues exactly one business-table create. The
    # automatic blank Lark table is subsequently removed by schema enforcement.
    create_calls = []

    def fake_create_req(method, path, token=None, body=None, timeout=15):
        del token, timeout
        create_calls.append((method, path, body))
        if method == "POST" and path.endswith("/apps"):
            return {"code": 0, "data": {"app": {
                "app_token": "new-app",
                "url": "https://example.test/base/new-app?table=default&view=old",
            }}}
        if method == "POST" and path.endswith("/apps/new-app/tables"):
            assert body["table"]["name"] == "Recruiting20260722"
            assert body["table"]["default_view_name"] == "All Candidates"
            return {"code": 0, "data": {"table_id": "daily-table"}}
        raise AssertionError((method, path, body))

    try:
        lark_bitable.tenant_token = lambda: ("fixture-token", None)
        lark_bitable._req = fake_create_req
        lark_bitable.ensure_channel_base_schema = lambda *args, **kwargs: {"ok": True}
        created = lark_bitable.create_channel_base(
            "Recruiting20260722",
            ["Customer Service Representative"],
            ["LinkedIn", "Other"],
            ["Interview", "Hired"],
            hr_names=["HR One"],
            table_name="Recruiting20260722",
        )
    finally:
        lark_bitable.tenant_token = original_token
        lark_bitable._req = original_req
        lark_bitable.ensure_channel_base_schema = original_ensure

    assert created["ok"] is True
    assert created["pipeline_table_id"] == "daily-table"
    assert created["url"] == "https://example.test/base/new-app?table=daily-table"
    table_posts = [call for call in create_calls
                   if call[0] == "POST" and call[1].endswith("/tables")]
    assert len(table_posts) == 1

    # A truncated full-table read must fail closed at the explicit safety
    # boundary rather than silently ignoring later rows.
    page_calls = []

    def fake_unbounded_pages(method, path, token=None, body=None, timeout=15):
        del method, token, body, timeout
        page_calls.append(path)
        return {"code": 0, "data": {
            "items": [], "has_more": True,
            "page_token": "page-%d" % len(page_calls),
        }}

    try:
        lark_bitable.tenant_token = lambda: ("fixture-token", None)
        lark_bitable._req = fake_unbounded_pages
        limit = lark_bitable.list_pipeline_records("app-token", "daily-table")
    finally:
        lark_bitable.tenant_token = original_token
        lark_bitable._req = original_req

    assert not limit["ok"] and "25,000-row" in limit["error"]
    assert len(page_calls) == 50

    print("One-table daily Lark recruiting contract: PASSED")
    print("Blank default-table cleanup is bounded and safe: PASSED")
    print("Legacy field-name migration remains compatible: PASSED")


if __name__ == "__main__":
    main()
