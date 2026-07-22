import datetime
import hashlib

import channel_pipeline_schema as pipeline_schema
import channel_report
import channel_sheet_service


class FakeDatabase:
    def __init__(self):
        self.applications = {}
        self.events = {}
        self.settings = {}

    def set_setting(self, key, value):
        self.settings[key] = value

    def get_candidate_application_by_lark(self, record_id):
        row = self.applications.get(record_id)
        return dict(row) if row else None

    def get_candidate_by_lark(self, record_id):
        del record_id
        return None

    def apply_candidate_application_command(self, **command):
        event_id = command["event_id"]
        payload_sha = command["payload_sha256"]
        if event_id in self.events:
            previous_sha, previous = self.events[event_id]
            if previous_sha != payload_sha:
                raise ValueError("event payload conflict")
            return {**previous, "idempotent": True}

        record_id = command["lark_record_id"]
        existing = self.applications.get(record_id)
        if existing:
            if command["expected_version"] != existing["record_version"]:
                raise ValueError("stale record version")
            for supplied, stored, label in (
                (command["name"], existing["name"], "Candidate Name"),
                (command["channel"], existing["channel"], "Source Channel"),
                (command["source_detail"], existing["source_detail"], "Other Source Details"),
                (command["job_request_id"], existing["job_request_id"], "Hiring Job"),
            ):
                if supplied != stored:
                    raise ValueError(label + " is protected")
            if existing["candidate_url"] and command["candidate_url"] != existing["candidate_url"]:
                raise ValueError("Candidate URL is protected")
            changed = any((
                command["stage"] != existing["current_stage"],
                command["hr_owner"] != existing["hr_owner"],
                command["cv_url"] != existing["cv_url"],
            ))
            if changed:
                existing.update({
                    "current_stage": command["stage"],
                    "hr_owner": command["hr_owner"],
                    "cv_url": command["cv_url"],
                    "record_version": existing["record_version"] + 1,
                    "stage_effective_date": command["stage_effective_date"],
                })
            result = {
                "created": False,
                "updated": changed,
                "idempotent": not changed,
                "application_ref": existing["application_ref"],
                "record_version": existing["record_version"],
            }
        else:
            application_ref = "APP-" + hashlib.sha256(record_id.encode()).hexdigest()[:12].upper()
            self.applications[record_id] = {
                "application_ref": application_ref,
                "candidate_id": len(self.applications) + 1,
                "name": command["name"],
                "candidate_url": command["candidate_url"],
                "channel": command["channel"],
                "source_detail": command["source_detail"],
                "job_request_id": command["job_request_id"],
                "current_stage": command["stage"],
                "hr_owner": command["hr_owner"],
                "cv_url": command["cv_url"],
                "record_version": 1,
                "entry_date": command["entry_date"],
                "stage_effective_date": command["stage_effective_date"],
            }
            result = {
                "created": True,
                "updated": False,
                "idempotent": False,
                "application_ref": application_ref,
                "record_version": 1,
            }
        self.events[event_id] = (payload_sha, dict(result))
        return result


class FakeLark:
    supports_revision_barrier = True

    def __init__(self, records):
        self.records = records
        self.calls = 0

    def list_pipeline_records(self, app_token, table_id):
        assert (app_token, table_id) == ("app-token", "pipeline-table")
        self.calls += 1
        return {"ok": True, "records": self.records}

    def update_pipeline_record_fields(self, *args):
        del args
        return {"ok": True}


class MutatingLark(FakeLark):
    def list_pipeline_records(self, app_token, table_id):
        response = super().list_pipeline_records(app_token, table_id)
        if self.calls == 2:
            changed = [dict(row) for row in self.records]
            changed[0] = {**changed[0], "last_modified_time": "changed-during-read"}
            return {"ok": True, "records": changed}
        return response


def record(record_id, *, name="Candidate A", channel="LinkedIn", detail="",
           job="Sales", hr="Asha", status="", candidate_url="", cv_url="",
           modified="1"):
    return {
        "record_id": record_id,
        "created_time": "1784678400000",
        "last_modified_time": modified,
        "fields": {
            "Candidate Name": name,
            "Candidate URL": candidate_url,
            "Source Channel": channel,
            pipeline_schema.OTHER_SOURCE_DETAIL: detail,
            "Hiring Job": job,
            "Assigned HR": hr,
            "Status": status,
            "CV": cv_url,
        },
    }


def main():
    lark_date_ms = int(datetime.datetime(
        2026, 7, 22, tzinfo=channel_sheet_service.INDIA_TIMEZONE,
    ).timestamp() * 1000)
    assert channel_sheet_service._lark_date(str(lark_date_ms), "") == "2026-07-22"

    assert [item["header"] for item in pipeline_schema.PIPELINE_COLUMNS] == [
        "Date", "Candidate Name", "Candidate URL", "Source Channel",
        pipeline_schema.OTHER_SOURCE_DETAIL, "Hiring Job", "Assigned HR",
        "Status", "CV",
    ]
    assert pipeline_schema.MANUAL_COLUMNS == ()
    assert channel_report.CHANNELS == [
        "LinkedIn", "Naukri", "Telegram", "Facebook", "WhatsApp",
        "Company Careers", "Employee Referral", "Recruitment Agency",
        "Job Fair / Offline", "Other",
    ]
    assert channel_report.PIPELINE_STATUS == [
        "Contacted / Awaiting Reply", "HR Screening", "Interview", "Offer",
        "Hired", "Rejected", "Withdrawn", "Resigned",
    ]

    database = FakeDatabase()
    jobs = [
        {"id": 7, "title": "Sales", "title_aliases": [], "status": "open"},
        {"id": 8, "title": "Closed Job", "title_aliases": [], "status": "closed"},
    ]
    rows = [
        record("rec-discovery", name="Discovered Candidate", hr="Asha",
               candidate_url="https://www.linkedin.com/in/discovered"),
        record("rec-external", name="External Candidate", channel="Other",
               detail="Local iGaming community", hr="Neha", status="Interview",
               cv_url="https://drive.example.test/cv/external"),
    ]
    lark = FakeLark(rows)
    config = {
        "app_token": "app-token",
        "pipeline_table_id": "pipeline-table",
        "schema_version": pipeline_schema.SCHEMA_VERSION,
    }
    first = channel_sheet_service.sync_lark_table(
        database, lark, config, jobs=jobs, channels=channel_report.CHANNELS,
        default_date="2026-07-22", synced_at="2026-07-22T12:00:00+00:00",
    )
    assert first["status"] == "synchronized", first
    assert first["created"] == 2 and first["updated"] == 0
    assert database.applications["rec-discovery"]["current_stage"] == "Pending"
    assert database.applications["rec-external"]["current_stage"] == "Interview"
    assert database.applications["rec-external"]["cv_url"].startswith("https://")

    second = channel_sheet_service.sync_lark_table(
        database, lark, config, jobs=jobs, channels=channel_report.CHANNELS,
        default_date="2026-07-22", synced_at="2026-07-22T12:01:00+00:00",
    )
    assert second["created"] == 0 and second["updated"] == 0, second
    assert len(database.applications) == 2

    rows[0]["last_modified_time"] = "2"
    rows[0]["fields"]["Status"] = "HR Screening"
    third = channel_sheet_service.sync_lark_table(
        database, lark, config, jobs=jobs, channels=channel_report.CHANNELS,
        default_date="2026-07-23", synced_at="2026-07-23T12:00:00+00:00",
    )
    assert third["updated"] == 1 and not third["errors"], third
    assert database.applications["rec-discovery"]["current_stage"] == "HR Screening"
    assert database.applications["rec-discovery"]["stage_effective_date"] == "2026-07-23"

    invalid_rows = [
        record("missing-owner", hr=""),
        record("bad-other", channel="Other", detail=""),
        record("wrong-detail", channel="LinkedIn", detail="not allowed"),
        record("closed-job", job="Closed Job"),
        record("bad-profile", candidate_url="http://not-secure.example.test/profile"),
        record("bad-cv", cv_url="javascript:bad"),
    ]
    invalid = channel_sheet_service.import_lark_pipeline_records(
        database, invalid_rows, jobs=jobs, channels=channel_report.CHANNELS,
        stages=["Pending", *channel_report.PIPELINE_STATUS],
        default_date="2026-07-23",
    )
    assert invalid["created"] == 0 and len(invalid["errors"]) == len(invalid_rows), invalid

    before = len(database.events)
    unstable = channel_sheet_service.sync_lark_table(
        database, MutatingLark([rows[0]]), config,
        jobs=jobs, channels=channel_report.CHANNELS,
        default_date="2026-07-23", synced_at="2026-07-23T12:02:00+00:00",
    )
    assert not unstable["ok"] and "changed during submission" in unstable["error"]
    assert len(database.events) == before

    legacy = channel_sheet_service.sync_lark_table(
        database, lark, {"app_token": "old", "pipeline_table_id": "old"},
        jobs=jobs, channels=channel_report.CHANNELS,
        default_date="2026-07-23", synced_at="now",
    )
    assert not legacy["ok"]

    print("One daily Recruiting table import service: PASSED")
    print("Exact fields, controlled jobs/sources/statuses and HR assignment: PASSED")
    print("Idempotency, protected identity and stable-read barrier: PASSED")


if __name__ == "__main__":
    main()
