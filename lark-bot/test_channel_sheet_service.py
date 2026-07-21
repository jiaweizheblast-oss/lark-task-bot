import channel_sheet_service
import channel_report
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
            "channel": channel, "source_detail": source_detail, "job_request_id": job_request_id,
            "lark_record_id": lark_record_id}
        return candidate_id

    def update_candidate(self, candidate_id, **fields):
        self.candidates[candidate_id].update(fields)

    def transition_candidate_stage(self, candidate_id, to_stage, effective_date, changed_by,
                                   rejection_reason, note, event_ref):
        self.candidates[candidate_id]["status"] = to_stage
        return {"event_ref": event_ref}


class FakeLark:
    def __init__(self, records, pipeline_records=None):
        self.records = records
        self.pipeline_records = pipeline_records or []
        self.calls = 0

    def list_channel_records(self, app_token, table_id):
        assert app_token == "app-token"
        assert table_id == "manual-table"
        self.calls += 1
        return {"ok": True, "records": self.records}

    def list_pipeline_records(self, app_token, table_id):
        assert app_token == "app-token" and table_id == "pipeline-table"
        return {"ok": True, "records": self.pipeline_records}


def main():
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
        "其他来源说明（选 Other 时必填）": "", "Job": "Sales", "Current Stage": "Interview 1",
        "Stage Date": "2026-07-21", "HR Owner": "HR-01", "Rejection Reason": "",
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
    assert database.rows[key]["new_resumes"] == 14
    assert len(database.rows) == 1

    # Re-submitting the online table remains one channel/day/job record.
    channel_sheet_service.sync_lark_table(
        database, lark, config, jobs=jobs, channels=channels,
        default_date="2026-07-21", synced_at="2026-07-21T12:01:00+00:00",
    )
    assert len(database.rows) == 1 and len(database.candidates) == 1 and lark.calls == 2

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
    assert any("其他来源说明" in error for error in other_without_detail["errors"])

    legacy = channel_sheet_service.sync_lark_table(
        database, lark, {"app_token": "old", "manual_table_id": "old"},
        jobs=jobs, channels=channels, default_date="2026-07-21", synced_at="now",
    )
    assert not legacy["ok"] and lark.calls == 2

    lark_headers = [item["field_name"] for item in
                    lark_bitable._channel_fields_spec(["Sales"], channels)]
    for shared_header in ("日期", "招聘渠道", "其他来源说明（选 Other 时必填）", "关联职位", "今日新增简历数",
                          "初筛通过数", "已推荐面试数", "已拒绝数", "备注", "填写人"):
        assert shared_header in lark_headers
    for expected_source in ("Talent Discovery", "LinkedIn", "Naukri", "Telegram",
                            "Facebook", "WhatsApp", "iGamingJobs", "SiGMA Careers", "Other"):
        assert expected_source in channel_report.CHANNELS
    assert channel_report.validate_source("Other", "")
    assert not channel_report.validate_source("Other", "Local iGaming group")

    print("Channel website/Lark shared application service: PASSED")
    print("Natural-key upsert, corrections, controlled fields, and legacy fail-closed: PASSED")


if __name__ == "__main__":
    main()
