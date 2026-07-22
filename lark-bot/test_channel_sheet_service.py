import datetime

import channel_sheet_service
import channel_report
import channel_pipeline_schema as pipeline_schema
import lark_bitable


class FakeDatabase:
    def __init__(self):
        self.rows = {}
        self.settings = {}
        self.candidates = {}
        self.next_candidate_id = 1

    def upsert_channel_record(self, record_date, channel, job_request_id, filled_by,
                              new_resumes, passed_screening, recommended, rejected, note,
                              source_detail=""):
        key = (record_date, channel, source_detail, job_request_id)
        self.rows[key] = {
            "record_date": record_date, "channel": channel,
            "job_request_id": job_request_id, "filled_by": filled_by,
            "new_resumes": new_resumes, "passed_screening": passed_screening,
            "recommended": recommended, "rejected": rejected, "note": note,
        }

    def set_setting(self, key, value):
        self.settings[key] = value

    def get_candidate_by_lark(self, record_id):
        return next((row for row in self.candidates.values() if row.get("lark_record_id") == record_id), None)

    def get_candidate(self, candidate_id):
        return self.candidates.get(candidate_id)

    def create_candidate(self, apply_date, name, channel, job_request_id, status, note,
                         filled_by, source, ext_ref, lark_record_id, source_detail):
        candidate_id = self.next_candidate_id; self.next_candidate_id += 1
        self.candidates[candidate_id] = {"id": candidate_id, "status": status, "name": name,
            "apply_date": apply_date,
            "channel": channel, "source_detail": source_detail, "job_request_id": job_request_id,
            "lark_record_id": lark_record_id}
        return candidate_id

    def update_candidate(self, candidate_id, **fields):
        self.candidates[candidate_id].update(fields)

    def transition_candidate_stage(self, candidate_id, to_stage, effective_date, changed_by,
                                   rejection_reason, note, event_ref):
        self.candidates[candidate_id]["status"] = to_stage
        self.candidates[candidate_id]["stage_date"] = effective_date
        return {"event_ref": event_ref}


class FakeLark:
    def __init__(self, records, pipeline_records=None):
        self.records = records
        self.pipeline_records = pipeline_records or []
        self.calls = 0
        self.writebacks = []

    def list_channel_records(self, app_token, table_id):
        assert app_token == "app-token"
        assert table_id == "manual-table"
        self.calls += 1
        return {"ok": True, "records": self.records}

    def list_pipeline_records(self, app_token, table_id):
        assert app_token == "app-token" and table_id == "pipeline-table"
        return {"ok": True, "records": self.pipeline_records}

    def update_pipeline_record_fields(self, app_token, table_id, record_id, fields):
        assert app_token == "app-token" and table_id == "pipeline-table"
        self.writebacks.append((record_id, dict(fields)))
        for record in self.pipeline_records:
            if record.get("record_id") == record_id:
                record.setdefault("fields", {}).update(fields)
        return {"ok": True, "updated": True}


def main():
    lark_date_ms = int(datetime.datetime(
        2026, 7, 21, tzinfo=channel_sheet_service.INDIA_TIMEZONE).timestamp() * 1000)
    assert channel_sheet_service._lark_date(str(lark_date_ms), "") == "2026-07-21"
    database = FakeDatabase()
    jobs = [{"id": 7, "title": "Sales"}]
    channels = channel_report.CHANNELS
    parsed = {"rows": [{
        "record_date": "2026-07-21", "channel": "Recruitment Event / Job Fair", "job_request_id": 7,
        "filled_by": "free text", "new_resumes": 12, "passed_screening": 5,
        "recommended": 2, "rejected": 3, "note": "website",
    }], "skipped": 2, "errors": []}
    first = channel_sheet_service.import_channel_rows(database, parsed, owner="HR-01")
    assert first["applied"] == 1
    key = ("2026-07-21", "Recruitment Event / Job Fair", "", 7)
    assert database.rows[key]["filled_by"] == "HR-01"

    # Same natural key is an upsert: a correction replaces one row, never doubles counts.
    parsed["rows"][0]["new_resumes"] = 13
    channel_sheet_service.import_channel_rows(database, parsed, owner="HR-01")
    assert len(database.rows) == 1 and database.rows[key]["new_resumes"] == 13

    lark = FakeLark([{"record_id": "rec-001", "fields": {
        "日期": "2026-07-21", "招聘渠道": "Recruitment Event / Job Fair", "关联职位": "Sales",
        "今日新增简历数": "14", "初筛通过数": 6, "已推荐面试数": 2, "已拒绝数": 3,
        "备注": "lark correction", "填写人": "HR-01",
    }}], pipeline_records=[{"record_id": "pipeline-1", "fields": {
        "Candidate": "Maya", "Entry Date": "2026-07-21", "Source Channel": "iGamingJobs",
        pipeline_schema.OTHER_SOURCE_DETAIL: "", "Job": "Sales", "Current Stage": "Interview 1",
        "Stage Started On": "2099-12-31", "HR Owner": "HR-01", "Rejection Reason": "",
        "Note": "direct entry", "System ID": "",
    }}])
    config = {"app_token": "app-token", "pipeline_table_id": "pipeline-table",
              "manual_table_id": "manual-table", "schema_version": "channel-analytics-v2"}
    synced = channel_sheet_service.sync_lark_table(
        database, lark, config, jobs=jobs, channels=channels,
        default_date="2026-07-21", synced_at="2026-07-21T12:00:00+00:00",
    )
    assert synced["applied"] == 1
    assert synced["created"] == 1
    assert database.candidates[1]["status"] == "Interview 1"
    assert database.candidates[1]["stage_date"] == "2026-07-21"
    assert lark.writebacks == []
    assert synced["system_field_writebacks"] == 0
    assert database.rows[key]["new_resumes"] == 14
    assert len(database.rows) == 1

    # Re-submitting the online table remains one channel/day/job record.
    channel_sheet_service.sync_lark_table(
        database, lark, config, jobs=jobs, channels=channels,
        default_date="2026-07-21", synced_at="2026-07-21T12:01:00+00:00",
    )
    assert len(database.rows) == 1 and len(database.candidates) == 1 and lark.calls == 2

    # When HR changes Current Stage, the service assigns today's Kolkata date.
    # Legacy date fields are ignored; workflow dates are owned by the service.
    lark.pipeline_records[0]["fields"]["Current Stage"] = "Interview 2 / Final"
    lark.pipeline_records[0]["fields"]["Stage Started On"] = "1900-01-01"
    lark.pipeline_records[0]["fields"]["Entry Date"] = "1900-01-01"
    changed = channel_sheet_service.sync_lark_table(
        database, lark, config, jobs=jobs, channels=channels,
        default_date="2026-07-22", synced_at="2026-07-22T12:00:00+00:00",
    )
    assert database.candidates[1]["status"] == "Interview 2 / Final"
    assert database.candidates[1]["stage_date"] == "2026-07-22"
    assert lark.pipeline_records[0]["fields"]["Stage Started On"] == "1900-01-01"
    assert database.candidates[1]["apply_date"] == "2026-07-21"
    assert changed["stage_date_writebacks"] == 0

    bad = channel_sheet_service.import_lark_channel_records(
        database,
        [{"record_id": "bad", "fields": {"日期": "21/07/2026",
          "招聘渠道": "自由文本渠道", "关联职位": "Sales", "今日新增简历数": -1}}],
        jobs=jobs, default_date="2026-07-21", channels=channels,
    )
    assert bad["applied"] == 0 and bad["errors"] and len(database.rows) == 1

    other_without_detail = channel_sheet_service.import_lark_channel_records(
        database,
        [{"record_id": "other", "fields": {"日期": "2026-07-21",
          "招聘渠道": "Other", "关联职位": "Sales", "今日新增简历数": 1}}],
        jobs=jobs, default_date="2026-07-21", channels=channels,
    )
    assert other_without_detail["applied"] == 0
    assert any("Other Source Details is required" in error
               for error in other_without_detail["errors"])

    detail_without_other = channel_sheet_service.import_lark_channel_records(
        database,
        [{"record_id": "wrong-detail", "fields": {
            "Date": "2026-07-21", "Source Channel": "LinkedIn",
            pipeline_schema.OTHER_SOURCE_DETAIL: "must be rejected", "Job": "Sales",
            "New Resumes": 1,
        }}],
        jobs=jobs, default_date="2026-07-21", channels=channels,
    )
    assert detail_without_other["applied"] == 0
    assert any("only when Source Channel = Other" in error
               for error in detail_without_other["errors"])

    pipeline_detail_without_other = channel_sheet_service.import_lark_pipeline_records(
        database,
        [{"record_id": "pipeline-wrong-detail", "fields": {
            "Candidate": "No Import", "Source Channel": "LinkedIn",
            pipeline_schema.OTHER_SOURCE_DETAIL: "must be rejected",
            "Job": "Sales", "Current Stage": "New Lead",
        }}],
        jobs=jobs, channels=channels,
        stages=("New Lead",), default_date="2026-07-21",
    )
    assert pipeline_detail_without_other["created"] == 0
    assert any("only when Source Channel = Other" in error
               for error in pipeline_detail_without_other["errors"])

    # Editing a requisition title stores both its old and current labels as
    # aliases.  Repeating the current title for the same stable job ID is not
    # an ambiguous match and must not reject a valid Open requisition.
    renamed_database = FakeDatabase()
    renamed_job = [{
        "id": 17,
        "title": "Customer Service Representative",
        "title_aliases": [
            "Customer Service REPRESENTATIVE",
            "Customer Service Representative",
        ],
        "status": "open",
    }]
    renamed_result = channel_sheet_service.import_lark_pipeline_records(
        renamed_database,
        [{"record_id": "renamed-job-row", "fields": {
            "Candidate": "Valid Candidate",
            "Source Channel": "Company Careers",
            pipeline_schema.OTHER_SOURCE_DETAIL: "",
            "Job": "  CUSTOMER   SERVICE REPRESENTATIVE  ",
            "Current Stage": "Contacted / Awaiting Reply",
            "HR Owner": "HR-01",
        }}],
        jobs=renamed_job, channels=channels,
        stages=channel_report.PIPELINE_STATUS,
        default_date="2026-07-22",
    )
    assert renamed_result["created"] == 1, renamed_result
    assert not renamed_result["errors"], renamed_result
    assert renamed_database.candidates[1]["job_request_id"] == 17

    legacy = channel_sheet_service.sync_lark_table(
        database, lark, {"app_token": "old", "manual_table_id": "old"},
        jobs=jobs, channels=channels, default_date="2026-07-21", synced_at="now",
    )
    assert not legacy["ok"] and lark.calls == 3

    lark_headers = [item["field_name"] for item in
                    lark_bitable._channel_fields_spec(["Sales"], channels)]
    for shared_header in ("Date", "Source Channel", pipeline_schema.OTHER_SOURCE_DETAIL,
                          "Job", "New Resumes", "Passed Screening",
                          "Recommended for Interview", "Rejected", "Note", "HR Owner"):
        assert shared_header in lark_headers
    for expected_source in ("Talent Discovery", "LinkedIn", "Naukri", "Telegram",
                            "Facebook", "WhatsApp", "iGamingJobs", "Other"):
        assert expected_source in channel_report.CHANNELS
    for removed_source in ("Foundit / Monster", "Shine", "Apna", "WorkIndia",
                           "Job Hai", "Internshala", "Wellfound", "Glassdoor",
                           "Stack Overflow", "SiGMA Careers"):
        assert removed_source not in channel_report.CHANNELS
    active_series = channel_report.channel_series([
        {"record_date": "2026-07-21", "channel": "Shine", "new_resumes": 9,
         "passed_screening": 1, "recommended": 0, "rejected": 0}
    ], "day")
    assert "Shine" not in active_series
    assert channel_report.validate_source("Other", "")
    assert not channel_report.validate_source("Other", "Local iGaming group")
    assert channel_report.validate_source("LinkedIn", "must not be accepted")

    print("Channel website/Lark shared application service: PASSED")
    print("Natural-key upsert, corrections, controlled fields, and legacy fail-closed: PASSED")


if __name__ == "__main__":
    main()
