import sqlite3
from pathlib import Path

import channel_pipeline_schema as schema
import db as nexus_db


ROOT = Path(__file__).resolve().parent


def test_operational_jobs_and_one_table_contract():
    assert schema.SCHEMA_VERSION == "daily-recruiting-table-v20"
    assert [column["header"] for column in schema.PIPELINE_COLUMNS] == [
        "Date", "Candidate Name", "Candidate URL", "Source Channel",
        "Other Source Details (Required only when Source Channel is Other)",
        "Hiring Job", "Assigned HR", "Status", "CV",
    ]
    assert schema.MANUAL_COLUMNS == ()
    assert schema.base_name_for("2026-07-22") == "Recruiting20260722"
    assert schema.table_name_for("2026-07-22") == "Recruiting20260722"

    html = (ROOT / "panel.html").read_text(encoding="utf-8")
    assert "Operational Job Requisitions" in html
    assert "Advanced: link a Talent Discovery search profile" in html
    assert "搜索与 HR 分配" in html
    assert "operational_job_ref" in html
    assert "正式配额" not in html[
        html.index('id="view-jobs"'):html.index('id="view-docs"')
    ]


def test_stage_lifecycle_is_forward_only():
    nexus_db._validate_application_stage_change("Pending", "HR Screening")
    nexus_db._validate_application_stage_change("HR Screening", "Interview")
    nexus_db._validate_application_stage_change("Hired", "Resigned")
    for current_stage, next_stage in (
        ("Interview", "HR Screening"),
        ("Rejected", "Pending"),
        ("Hired", "Withdrawn"),
        ("Resigned", "Hired"),
    ):
        try:
            nexus_db._validate_application_stage_change(current_stage, next_stage)
        except ValueError:
            pass
        else:
            raise AssertionError(
                f"Unsafe stage change was accepted: {current_stage} -> {next_stage}"
            )


def test_restartable_application_migration_contract():
    sql = (ROOT / "schema.sql").read_text(encoding="utf-8")
    assert "candidate_application" in sql
    assert "record_type='search_profile'" in sql
    assert "ON DELETE RESTRICT" in sql
    assert "ON CONFLICT (candidate_id, job_request_id) DO NOTHING" not in sql
    assert "APP-RECOVER-" in sql
    assert "schema_migration_anomaly" in sql
    assert "a.job_request_id IS NOT DISTINCT FROM c.job_request_id" in sql
    assert "ON CONFLICT DO NOTHING" in sql
    for unsafe_sql in (
        "CONCURRENTLY", "VACUUM", "REINDEX", "CREATE DATABASE",
        "DROP DATABASE", "ALTER SYSTEM",
    ):
        assert unsafe_sql not in sql.upper()

    migration_db = sqlite3.connect(":memory:")
    migration_db.execute("""CREATE TABLE app(
        application_ref TEXT NOT NULL UNIQUE,
        candidate_id INTEGER NOT NULL,
        job_request_id INTEGER,
        UNIQUE(candidate_id, job_request_id))""")
    migration_db.execute("INSERT INTO app VALUES('APP-0000000001',1,NULL)")
    try:
        migration_db.execute("""INSERT INTO app VALUES('APP-0000000001',1,NULL)
            ON CONFLICT(candidate_id,job_request_id) DO NOTHING""")
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("Nullable legacy conflict was silently misclassified")
    migration_db.execute("""INSERT INTO app VALUES('APP-0000000001',1,NULL)
        ON CONFLICT DO NOTHING""")
    assert migration_db.execute("SELECT COUNT(*) FROM app").fetchone()[0] == 1
    migration_db.close()


def main():
    test_operational_jobs_and_one_table_contract()
    test_stage_lifecycle_is_forward_only()
    test_restartable_application_migration_contract()
    print("Operational/Search Profile separation: PASSED")
    print("One-table job catalog and restartable migration contract: PASSED")


if __name__ == "__main__":
    main()
