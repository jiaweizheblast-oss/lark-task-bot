import io

import bot
import sheet_io


class FakeChannelStore:
    def __init__(self):
        self.rows = {}
        self.settings = {
            "lark_channel_app_token": "app-token",
            "lark_channel_pipeline_table_id": "pipeline-table",
            "lark_channel_manual_table_id": "manual-table",
            "lark_channel_url": "https://example.test/base/channel",
            "lark_channel_schema_version": "channel-analytics-v2",
        }
        self.candidates = {}
        self.next_candidate_id = 1

    def upsert(self, record_date, channel, job_request_id, filled_by,
               new_resumes, passed_screening, recommended, rejected, note,
               source_detail=""):
        self.rows[(record_date, channel, source_detail, job_request_id)] = {
            "new_resumes": new_resumes, "passed_screening": passed_screening,
            "recommended": recommended, "rejected": rejected, "filled_by": filled_by,
        }

    def create_candidate(self, apply_date, name, channel, job_request_id, status,
                         note, filled_by, source, ext_ref, lark_record_id, source_detail):
        candidate_id = self.next_candidate_id
        self.next_candidate_id += 1
        self.candidates[candidate_id] = {"id": candidate_id, "apply_date": apply_date,
            "name": name, "channel": channel, "source_detail": source_detail,
            "job_request_id": job_request_id, "status": status, "note": note,
            "filled_by": filled_by, "ext_ref": ext_ref}
        return candidate_id

    def transition(self, candidate_id, to_stage, effective_date, changed_by,
                   rejection_reason, note, event_ref):
        old = self.candidates[candidate_id]["status"]
        self.candidates[candidate_id]["status"] = to_stage
        return {"from_stage": old, "to_stage": to_stage, "event_ref": event_ref or "generated"}


def main():
    store = FakeChannelStore()
    jobs = [{"id": 7, "title": "Sales", "target_headcount": 1, "target_resume_count": 10}]
    bot.PANEL_PASSWORD = "channel-route-password"
    bot.PUBLIC_BASE_URL = "https://nexus.example.test"
    bot.db.list_job_requests = lambda only_open=False: jobs
    bot.db.get_settings = lambda: store.settings
    bot.db.set_setting = lambda key, value: store.settings.__setitem__(key, value)
    bot.db.upsert_channel_record = store.upsert
    bot.db.is_admin = lambda open_id: open_id == "admin-open-id"
    bot.db.get_job_request = lambda job_id: jobs[0] if job_id == 7 else None
    bot.db.create_candidate = store.create_candidate
    bot.db.get_candidate = lambda candidate_id: store.candidates.get(candidate_id)
    bot.db.get_candidate_by_ext_ref = lambda ref: next((c for c in store.candidates.values() if c.get("ext_ref") == ref), None)
    bot.db.update_candidate = lambda candidate_id, **fields: store.candidates[candidate_id].update(fields)
    bot.db.transition_candidate_stage = store.transition

    workbook = sheet_io.build_xlsx(
        sheet_io.pipeline_columns(["Sales"]),
        prefill_rows=[{
            "record_date": "2026-07-21", "name": "Asha", "channel": "LinkedIn",
            "source_detail": "", "job": "Sales", "status": "HR Screening",
            "stage_date": "2026-07-21", "rejection_reason": "",
            "note": "route", "filled_by": "ignored", "cand_id": "", "row_ref": "manual-test-row",
        }],
        sheet_title="未建档批量统计",
    )
    client = bot.app.test_client()
    uploaded = client.post(
        "/api/channel/upload",
        headers={"X-Auth": bot.PANEL_PASSWORD},
        data={"by": "HR-01", "d": "2026-07-21",
              "file": (io.BytesIO(workbook), "channel.xlsx")},
        content_type="multipart/form-data",
    )
    assert uploaded.status_code == 200
    assert uploaded.get_json()["created"] == 1
    key = ("2026-07-21", "LinkedIn", "", 7)
    assert store.candidates[1]["status"] == "HR Screening"
    assert store.candidates[1]["filled_by"] == "HR-01"

    repeated_upload = client.post(
        "/api/channel/upload", headers={"X-Auth": bot.PANEL_PASSWORD},
        data={"by": "HR-01", "d": "2026-07-21",
              "file": (io.BytesIO(workbook), "channel.xlsx")},
        content_type="multipart/form-data")
    assert repeated_upload.status_code == 200
    assert repeated_upload.get_json()["created"] == 0
    assert repeated_upload.get_json()["updated"] == 1
    assert len(store.candidates) == 1

    rejected_csv = client.post(
        "/api/channel/upload",
        headers={"X-Auth": bot.PANEL_PASSWORD},
        data={"file": (io.BytesIO(b"a,b"), "channel.csv")},
        content_type="multipart/form-data",
    )
    assert rejected_csv.status_code == 400

    bot.lark_bitable.list_pipeline_records = lambda app, table: {"ok": True, "records": []}
    bot.lark_bitable.list_channel_records = lambda app, table: {"ok": True, "records": [{
        "record_id": "rec-1", "fields": {
            "日期": "2026-07-21", "招聘渠道": "LinkedIn", "关联职位": "Sales",
            "今日新增简历数": 11, "初筛通过数": 5,
            "已推荐面试数": 2, "已拒绝数": 3, "填写人": "HR-01",
        },
    }]}
    synced = client.post("/api/lark/pull", headers={"X-Auth": bot.PANEL_PASSWORD})
    assert synced.status_code == 200 and synced.get_json()["applied"] == 1
    assert store.rows[key]["new_resumes"] == 11

    missing_other_detail = client.post("/api/candidates", headers={"X-Auth": bot.PANEL_PASSWORD}, json={
        "apply_date": "2026-07-21", "stage_date": "2026-07-21", "name": "Ravi",
        "channel": "Other", "job_request_id": 7, "status": "Interview 1",
    })
    assert missing_other_detail.status_code == 422

    direct_entry = client.post("/api/candidates", headers={"X-Auth": bot.PANEL_PASSWORD}, json={
        "apply_date": "2026-07-21", "stage_date": "2026-07-21", "name": "Ravi",
        "channel": "Naukri", "source_detail": "", "job_request_id": 7,
        "status": "Interview 1", "filled_by": "HR-01",
    })
    assert direct_entry.status_code == 200
    candidate_id = direct_entry.get_json()["id"]
    assert store.candidates[candidate_id]["status"] == "Interview 1"

    rejected_without_reason = client.patch(
        "/api/candidates/%d" % candidate_id, headers={"X-Auth": bot.PANEL_PASSWORD}, json={
            "name": "Ravi", "channel": "Naukri", "job_request_id": 7,
            "status": "Rejected", "stage_date": "2026-07-22",
        })
    assert rejected_without_reason.status_code == 422

    rejected = client.patch(
        "/api/candidates/%d" % candidate_id, headers={"X-Auth": bot.PANEL_PASSWORD}, json={
            "name": "Ravi", "channel": "Naukri", "job_request_id": 7,
            "status": "Rejected", "stage_date": "2026-07-22",
            "rejection_reason": "Compensation mismatch", "filled_by": "HR-01",
        })
    assert rejected.status_code == 200
    assert store.candidates[candidate_id]["status"] == "Rejected"

    cards = []
    bot.send_card = lambda chat_id, card: cards.append((chat_id, card)) or "message-id"
    bot.handle_dm("admin-open-id", "chat-id", "/channel_sheet")
    assert cards[-1][1]["header"]["title"]["content"].startswith("Channel Analytics")
    bot.handle_dm("admin-open-id", "chat-id", "/submit_channel_sheet")
    assert cards[-1][1]["header"]["title"]["content"] == "渠道表已同步"
    assert store.rows[key]["new_resumes"] == 11

    status = client.get(
        "/api/integration/v1/channel/status",
        headers={"Authorization": "Bearer " + "w" * 32},
    )
    assert status.status_code == 401
    bot.NEXUS_TALENT_WORKER_TOKEN = "w" * 32
    status = client.get(
        "/api/integration/v1/channel/status",
        headers={"Authorization": "Bearer " + "w" * 32},
    )
    assert status.status_code == 200 and status.get_json()["configured"] is True
    submitted = client.post(
        "/api/integration/v1/channel/submit",
        headers={"Authorization": "Bearer " + "w" * 32},
    )
    assert submitted.status_code == 200 and submitted.get_json()["applied"] == 1

    blocked_reset = client.post(
        "/api/lark/reconnect", headers={"X-Auth": bot.PANEL_PASSWORD},
        json={"confirmation": "RESET_UNSYNCED_CHANNEL_LINK"},
    )
    assert blocked_reset.status_code == 409
    store.settings["lark_channel_last_sync"] = ""
    repaired = client.post(
        "/api/lark/reconnect", headers={"X-Auth": bot.PANEL_PASSWORD},
        json={"confirmation": "RESET_UNSYNCED_CHANNEL_LINK"},
    )
    assert repaired.status_code == 200
    assert repaired.get_json()["candidate_data_changed"] is False
    assert store.settings["lark_channel_app_token"] == ""

    print("Channel website upload and Lark/Bot submission routes: PASSED")
    print("XLSX-only boundary, shared upsert service, source rules, direct-stage entry, and Bot commands: PASSED")


if __name__ == "__main__":
    main()
