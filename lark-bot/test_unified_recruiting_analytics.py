import datetime
from pathlib import Path

import channel_report


ROOT = Path(__file__).resolve().parent


def _count(rows):
    return {item["label"]: item["count"] for item in rows}


def main():
    snapshot_rows = [
        {
            "job_request_id": 7,
            "job_title": "Sales Member",
            "status": "New Lead",
            "channel": "LinkedIn",
            "filled_by": "HR1",
            "baseline_import": True,
        },
        {
            "job_request_id": 7,
            "job_title": "Sales Member",
            "status": "Interview 1",
            "channel": "Legacy Source",
            "filled_by": "HR2",
            "baseline_import": False,
        },
        {
            "job_request_id": 8,
            "job_title": "Customer Support",
            "status": "Hired",
            "channel": "Naukri",
            "filled_by": "",
            "baseline_import": False,
        },
    ]
    snapshot = channel_report.current_snapshot(snapshot_rows)
    assert snapshot["total"] == 3
    assert snapshot["baseline_count"] == 1
    assert snapshot["history_complete_count"] == 2
    assert snapshot["in_progress"] == 2
    assert snapshot["hired"] == 1
    assert snapshot["unassigned_hr"] == 1
    assert _count(snapshot["by_status"]) == {
        "Pending": 1, "Interview": 1, "Hired": 1,
    }
    assert _count(snapshot["by_channel"])["Legacy Source"] == 1
    assert channel_report.current_snapshot(snapshot_rows, 7)["total"] == 2

    events = [
        {
            "record_date": datetime.date(2026, 7, 22),
            "channel": "LinkedIn",
            "job_request_id": 7,
            "new_resumes": 1,
            "passed_screening": 0,
            "recommended": 0,
            "rejected": 0,
        },
        {
            "record_date": datetime.date(2026, 7, 22),
            "channel": "Legacy Source",
            "job_request_id": 7,
            "new_resumes": 0,
            "passed_screening": 1,
            "recommended": 1,
            "rejected": 0,
        },
    ]
    jobs = [{
        "id": 7,
        "title": "Sales Member",
        "status": "open",
        "target_resume_count": 10,
        "target_headcount": 2,
    }]
    analytics = channel_report.analytics(
        events,
        jobs,
        dfrom=datetime.date(2026, 7, 22),
        dto=datetime.date(2026, 7, 22),
        data_space="identity_derived",
    )
    assert analytics["space"] == "identity_derived"
    assert analytics["identity_derived"]["available"] is True
    assert analytics["summary"]["new"] == 1
    assert analytics["summary"]["passed"] == 1
    assert analytics["summary"]["recommended"] == 1
    assert "Legacy Source" in analytics["channel_series"]
    assert "Legacy Source" not in channel_report.CHANNELS

    database_source = (ROOT / "db.py").read_text(encoding="utf-8")
    assert "a.baseline_import=FALSE" in database_source
    assert "e.baseline_import=FALSE" in database_source
    assert "e.to_stage IN ('Interview','Offer','Hired')" in database_source
    assert "e.to_stage IN ('Offer','Hired')" in database_source

    panel = (ROOT / "panel.html").read_text(encoding="utf-8")
    for element_id in (
        "cSnapshotKpis", "cStatusChart", "cSourceMixChart",
        "cHrWorkloadChart", "cJobMixChart", "cActivityKpis",
        "cTrendChart", "cChannelChart", "cJobProgress",
    ):
        assert panel.count('id="%s"' % element_id) == 1, element_id
    assert "Current Recruiting Portfolio" in panel
    assert "Recruiting Activity" in panel
    assert "Entered Interview" in panel
    assert "Reached Offer" in panel

    print("Current snapshot includes baseline without inventing history: PASSED")
    print("Forward-only activity and legacy-stage compatibility: PASSED")
    print("Four current charts plus three activity/result views: PASSED")


if __name__ == "__main__":
    main()
