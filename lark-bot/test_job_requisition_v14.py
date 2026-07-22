import io
from pathlib import Path

from openpyxl import load_workbook

import channel_pipeline_schema as schema
import sheet_io


KEY = "v14-job-catalog-row-signing-key-2026-07-22"


def _filled(workbook, *, job_title="CSR"):
    wb = load_workbook(io.BytesIO(workbook))
    ws = wb[schema.PIPELINE_TABLE_NAME]
    headers = {cell.value: cell.column for cell in ws[1]}
    ws.cell(2, headers["Candidate"], "Candidate One")
    ws.cell(2, headers["Source Channel"], "LinkedIn")
    ws.cell(2, headers["Job"], job_title)
    ws.cell(2, headers["Current Stage"], "New Lead")
    out = io.BytesIO(); wb.save(out); return out.getvalue()


def main():
    original = [{"id": 1, "job_ref": "REQ-CSR", "title": "CSR",
                 "status": "open", "catalog_revision": 3}]
    workbook = sheet_io.build_pipeline_template_xlsx(
        original, "2026-07-22", "HR-01", [], signing_key=KEY)

    # A rename after download is safe because the signed catalog resolves by
    # stable job_ref, never by the current display title.
    renamed = [{"id": 1, "job_ref": "REQ-CSR", "title": "Customer Service Representative",
                "status": "open", "catalog_revision": 4}]
    parsed = sheet_io.parse_pipeline_sheet(
        _filled(workbook), "ChannelAnalytics_20260722.xlsx", renamed,
        "HR-01", "2026-07-22", signing_key=KEY)
    assert not parsed["errors"] and parsed["rows"][0]["job_request_id"] == 1
    assert parsed["rows"][0]["job_ref"] == "REQ-CSR"

    # A job closed after workbook generation rejects new rows but does not
    # invalidate the artifact or destroy prior history.
    closed = [{**renamed[0], "status": "closed"}]
    rejected = sheet_io.parse_pipeline_sheet(
        _filled(workbook), "ChannelAnalytics_20260722.xlsx", closed,
        "HR-01", "2026-07-22", signing_key=KEY)
    assert any("Open requisition" in error for error in rejected["errors"])

    # HR commands are editable, but signed system identity is not.
    tampered = load_workbook(io.BytesIO(_filled(workbook)))
    ws = tampered[schema.PIPELINE_TABLE_NAME]
    headers = {cell.value: cell.column for cell in ws[1]}
    ws.cell(2, headers["Row Ref"], "forged-row")
    out = io.BytesIO(); tampered.save(out)
    bad = sheet_io.parse_pipeline_sheet(
        out.getvalue(), "ChannelAnalytics_20260722.xlsx", original,
        "HR-01", "2026-07-22", signing_key=KEY)
    assert any("signed system identity" in error for error in bad["errors"])

    sql = Path("schema.sql").read_text(encoding="utf-8")
    assert "candidate_application" in sql
    assert "record_type='search_profile'" in sql
    assert "ON DELETE RESTRICT" in sql
    assert "a.application_ref='APP-' || lpad(e.candidate_id::text,10,'0')" in sql
    html = Path("panel.html").read_text(encoding="utf-8")
    assert "Operational Job Requisitions" in html
    assert "正式配额" not in html[html.index('id="view-jobs"'):html.index('id="view-docs"')]
    print("Operational/Search Profile separation and application migration: PASSED")
    print("Frozen job catalog, rename compatibility, closure guard, row HMAC: PASSED")


if __name__ == "__main__":
    main()
