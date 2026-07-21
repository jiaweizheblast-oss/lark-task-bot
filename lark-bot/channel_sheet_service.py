"""Shared Channel Analytics table application service.

Both the website XLSX upload and the Lark/Bot submission path normalize into
one row per (report date, manual channel code, job). This module deliberately
does not write Talent Discovery candidate state.
"""
from __future__ import annotations

import datetime
import hashlib
from typing import Any, Mapping, Sequence

import channel_pipeline_schema as pipeline_schema


def _text(value: Any) -> str:
    return str(value or "").strip()


def _integer(value: Any) -> int:
    text = _text(value)
    if text == "":
        return 0
    number = int(float(text))
    if number < 0:
        raise ValueError("数量不能为负数")
    return number


def _valid_date(value: str) -> bool:
    try:
        datetime.date.fromisoformat(value[:10])
        return True
    except (TypeError, ValueError):
        return False


def _field(fields: Mapping[str, Any], key: str, *, manual: bool = False) -> Any:
    """Read a canonical field while accepting previous workbook/Base labels."""
    specs = pipeline_schema.MANUAL_COLUMNS if manual else pipeline_schema.PIPELINE_COLUMNS
    spec = next(item for item in specs if item["key"] == key)
    for name in (spec["header"], *spec.get("aliases", ())):
        value = fields.get(name)
        if value is not None and _text(value) != "":
            return value
    return ""


def import_channel_rows(database, parsed: Mapping[str, Any], *, owner: str = "") -> dict[str, Any]:
    """Upsert canonical manual channel rows from any trusted parser."""
    applied = 0
    errors = list(parsed.get("errors") or [])
    for row in parsed.get("rows") or []:
        try:
            database.upsert_channel_record(
                row["record_date"], _text(row.get("channel")), row["job_request_id"],
                owner or _text(row.get("filled_by")),
                _integer(row.get("new_resumes")), _integer(row.get("passed_screening")),
                _integer(row.get("recommended")), _integer(row.get("rejected")),
                _text(row.get("note")), _text(row.get("source_detail")),
            )
            applied += 1
        except Exception as error:
            errors.append("入库失败（%s/%s）：%s" %
                          (row.get("channel"), row.get("record_date"), error))
    return {"ok": True, "applied": applied, "imported": applied, "updated": 0,
            "skipped": int(parsed.get("skipped") or 0), "errors": errors}


def import_pipeline_rows(
    database, parsed: Mapping[str, Any], *, owner: str = "", default_date: str = "",
) -> dict[str, Any]:
    """Apply website Pipeline workbook rows without guessing candidate identity."""
    created = updated = 0
    errors = list(parsed.get("errors") or [])
    for index, row in enumerate(parsed.get("rows") or [], start=2):
        try:
            name, channel = _text(row.get("name")), _text(row.get("channel"))
            detail, stage = _text(row.get("source_detail")), _text(row.get("status")) or "New Lead"
            if not name or not channel or not row.get("job_request_id"):
                raise ValueError("Candidate、Source Channel 和 Job 必填")
            if channel == "Other" and not detail:
                raise ValueError("选择 Other 时必须填写其他来源说明")
            if channel != "Other" and detail:
                raise ValueError(
                    "Only rows with Source Channel = Other may contain Other Source Detail"
                )
            # The workbook generation date is not candidate data. Entry Date is
            # assigned at the first accepted import and never updated by HR.
            record_date = _text(default_date)[:10]
            if not _valid_date(record_date):
                raise ValueError("系统 Entry Date 必须是 YYYY-MM-DD")
            candidate_id_text = _text(row.get("cand_id"))
            existing = database.get_candidate(int(candidate_id_text)) if candidate_id_text.isdigit() else None
            if candidate_id_text and existing is None:
                raise ValueError("System ID 不存在；拒绝按姓名猜测身份")
            row_ref = _text(row.get("row_ref"))
            if not existing and row_ref:
                existing = database.get_candidate_by_ext_ref(row_ref)
            if not existing and not row_ref:
                raise ValueError("新候选人缺少系统 Row Ref；请使用系统生成的表")
            created_now = existing is None
            if existing:
                candidate_id = existing["id"]
                if _text(existing.get("name")) and name != _text(existing.get("name")):
                    raise ValueError("Candidate 是已有记录的系统身份字段，禁止修改")
                database.update_candidate(candidate_id,
                    channel=channel, source_detail=detail, job_request_id=row.get("job_request_id"),
                    note=_text(row.get("note")), filled_by=owner or _text(row.get("filled_by")))
                updated += 1
            else:
                candidate_id = database.create_candidate(record_date, name, channel,
                    row.get("job_request_id"), "New Lead", _text(row.get("note")),
                    owner or _text(row.get("filled_by")), "Excel", row_ref, "", detail)
                existing = {"status": "New Lead"}
                created += 1
            current_stage = _text(existing.get("status")) or "New Lead"
            if created_now or stage != current_stage:
                stage_date = default_date or record_date
                reason = _text(row.get("rejection_reason"))
                event_material = "|".join(("xlsx", str(candidate_id), stage, stage_date, reason))
                event_ref = "xlsx-" + hashlib.sha256(event_material.encode("utf-8")).hexdigest()[:32]
                database.transition_candidate_stage(candidate_id, stage, stage_date,
                    owner or _text(row.get("filled_by")), reason, _text(row.get("note")), event_ref)
        except Exception as error:
            errors.append("Pipeline 第%d行：%s" % (index, error))
    return {"ok": True, "created": created, "imported": created, "updated": updated,
            "skipped": int(parsed.get("skipped") or 0), "errors": errors}


def import_lark_channel_records(
    database,
    records: Sequence[Mapping[str, Any]],
    *,
    jobs: Sequence[Mapping[str, Any]],
    default_date: str,
    channels: Sequence[str],
) -> dict[str, Any]:
    """Validate and upsert Lark channel summary rows, failing closed per row."""
    title_to_id = {_text(job.get("title")): job.get("id") for job in jobs}
    channel_set = {_text(channel) for channel in channels}
    rows, errors, skipped = [], [], 0
    for index, record in enumerate(records, start=1):
        fields = record.get("fields") or {}
        channel = _text(_field(fields, "channel", manual=True))
        source_detail = _text(_field(fields, "source_detail", manual=True))
        job_title = _text(_field(fields, "job", manual=True))
        counts = [_field(fields, key, manual=True) for key in
                  ("new_resumes", "passed_screening", "recommended", "rejected")]
        if not channel and not job_title and not any(_text(value) for value in counts):
            skipped += 1
            continue
        date_value = _text(_field(fields, "record_date", manual=True))[:10] or default_date
        if not _valid_date(date_value):
            errors.append("Lark 第%d条：日期格式非法（应为 YYYY-MM-DD）" % index)
            continue
        if channel not in channel_set:
            errors.append("Lark 第%d条：渠道「%s」不在受控词表" % (index, channel))
            continue
        if channel == "Other" and not source_detail:
            errors.append("Lark 第%d条：选择 Other 时必须填写其他来源说明" % index)
            continue
        if channel != "Other" and source_detail:
            errors.append(
                "Lark row %d: Other Source Detail is allowed only when Source Channel = Other"
                % index
            )
            continue
        job_id = title_to_id.get(job_title)
        if not job_id:
            errors.append("Lark 第%d条：职位「%s」不存在或已停用" % (index, job_title))
            continue
        try:
            row = {
                "record_date": date_value,
                "channel": channel,
                "source_detail": source_detail,
                "job_request_id": job_id,
                "filled_by": _text(_field(fields, "filled_by", manual=True)),
                "new_resumes": _integer(_field(fields, "new_resumes", manual=True)),
                "passed_screening": _integer(_field(fields, "passed_screening", manual=True)),
                "recommended": _integer(_field(fields, "recommended", manual=True)),
                "rejected": _integer(_field(fields, "rejected", manual=True)),
                "note": _text(_field(fields, "note", manual=True)),
            }
        except (TypeError, ValueError) as error:
            errors.append("Lark 第%d条：数量非法（%s）" % (index, error))
            continue
        rows.append(row)
    return import_channel_rows(
        database,
        {"rows": rows, "errors": errors, "skipped": skipped},
    )


def import_lark_pipeline_records(
    database, records: Sequence[Mapping[str, Any]], *, jobs: Sequence[Mapping[str, Any]],
    channels: Sequence[str], stages: Sequence[str], default_date: str,
) -> dict[str, Any]:
    """Upsert source-neutral candidate applications and append stage changes."""
    title_to_id = {_text(job.get("title")): job.get("id") for job in jobs}
    channel_set, stage_set = set(channels), set(stages)
    created = updated = skipped = 0
    errors = []
    writebacks = []
    for index, record in enumerate(records, start=1):
        fields = record.get("fields") or {}
        name = _text(_field(fields, "name"))
        if not name and not any(_text(value) for value in fields.values()):
            skipped += 1
            continue
        channel = _text(_field(fields, "channel"))
        detail = _text(_field(fields, "source_detail"))
        job_id = title_to_id.get(_text(_field(fields, "job")))
        stage = _text(_field(fields, "status")) or "New Lead"
        # Entry Date is service-owned and is not exposed on the Lark HR form.
        entry_date = _text(default_date)[:10]
        reason = _text(_field(fields, "rejection_reason"))
        if not name:
            errors.append("Pipeline 第%d条：Candidate 必填" % index); continue
        if channel not in channel_set:
            errors.append("Pipeline 第%d条：Source Channel 非法" % index); continue
        if channel == "Other" and not detail:
            errors.append("Pipeline 第%d条：选择 Other 时必须填写其他来源说明" % index); continue
        if channel != "Other" and detail:
            errors.append(
                "Pipeline row %d: Other Source Detail is allowed only when Source Channel = Other"
                % index
            ); continue
        if not job_id:
            errors.append("Pipeline 第%d条：Job 不存在或已停用" % index); continue
        if stage not in stage_set:
            errors.append("Pipeline 第%d条：Current Stage 非法" % index); continue
        if not _valid_date(entry_date):
            errors.append("Pipeline 第%d条：Entry Date 必须是 YYYY-MM-DD" % index); continue
        if stage == "Rejected" and not reason:
            errors.append("Pipeline 第%d条：Rejected 必须填写 Rejection Reason" % index); continue
        record_id = _text(record.get("record_id"))
        try:
            existing = database.get_candidate_by_lark(record_id)
            system_id = _text(_field(fields, "cand_id"))
            if not existing and system_id.isdigit():
                existing = database.get_candidate(int(system_id))
            created_now = existing is None
            if existing:
                candidate_id = existing["id"]
                if _text(existing.get("name")) and name != _text(existing.get("name")):
                    raise ValueError("Candidate 是已有记录的系统身份字段，禁止修改")
                database.update_candidate(candidate_id,
                    channel=channel, source_detail=detail, job_request_id=job_id,
                    note=_text(_field(fields, "note")), filled_by=_text(_field(fields, "filled_by")),
                    lark_record_id=record_id)
                updated += 1
            else:
                candidate_id = database.create_candidate(
                    entry_date, name, channel, job_id, "New Lead", _text(_field(fields, "note")),
                    _text(_field(fields, "filled_by")), "Lark", "", record_id, detail)
                existing = {"status": "New Lead"}
                created += 1
            row_writeback = {}
            if system_id != str(candidate_id):
                row_writeback["System ID"] = str(candidate_id)
            current_stage = _text(existing.get("status")) or "New Lead"
            if created_now or stage != current_stage:
                # Stage Started On is system-owned. The database records the
                # canonical Kolkata processing date and never accepts a value
                # supplied by HR, Excel, or Lark.
                stage_date = default_date
                event_material = "|".join((record_id, str(candidate_id), stage, stage_date, reason))
                event_ref = "lark-" + hashlib.sha256(event_material.encode("utf-8")).hexdigest()[:32]
                database.transition_candidate_stage(candidate_id, stage, stage_date,
                    _text(_field(fields, "filled_by")), reason, _text(_field(fields, "note")), event_ref)
            if record_id and row_writeback:
                writebacks.append({"record_id": record_id, "fields": row_writeback})
        except Exception as error:
            errors.append("Pipeline 第%d条：%s" % (index, error))
    return {"ok": True, "created": created, "updated": updated, "skipped": skipped,
            "errors": errors, "_writebacks": writebacks}


def sync_lark_table(
    database,
    lark_client,
    config: Mapping[str, str],
    *,
    jobs: Sequence[Mapping[str, Any]],
    channels: Sequence[str],
    default_date: str,
    synced_at: str,
) -> dict[str, Any]:
    """Read the configured Lark channel table and apply it; used by web and Bot."""
    app_token = _text(config.get("app_token"))
    pipeline_table_id = _text(config.get("pipeline_table_id"))
    manual_table_id = _text(config.get("manual_table_id"))
    if (not app_token or not pipeline_table_id or not manual_table_id
            or config.get("schema_version") != "channel-analytics-v2"):
        return {"ok": False, "error": "Lark Channel Analytics 尚未配置或仍是旧版表"}
    pipeline_response = lark_client.list_pipeline_records(app_token, pipeline_table_id)
    if not pipeline_response.get("ok"):
        return pipeline_response
    manual_response = lark_client.list_channel_records(app_token, manual_table_id)
    if not manual_response.get("ok"):
        return manual_response
    pipeline_result = import_lark_pipeline_records(
        database, pipeline_response.get("records") or [], jobs=jobs, channels=channels,
        stages=("New Lead", "Contacted / Awaiting Reply", "HR Screening", "Interview 1",
                "Interview 2 / Final", "Offer", "Hired", "On Hold", "Rejected", "Withdrawn"),
        default_date=default_date,
    )
    writebacks = list(pipeline_result.pop("_writebacks", []))
    writeback_count = 0
    writeback_errors = []
    for item in writebacks:
        response = lark_client.update_pipeline_record_fields(
            app_token, pipeline_table_id, item["record_id"], item["fields"])
        if response.get("ok"):
            writeback_count += 1
        else:
            writeback_errors.append(response.get("error") or "System ID 回写失败")
    manual_result = import_lark_channel_records(
        database, manual_response.get("records") or [], jobs=jobs,
        default_date=default_date, channels=channels)
    result = {"ok": True, "pipeline": pipeline_result, "manual": manual_result,
              "created": pipeline_result["created"], "updated": pipeline_result["updated"],
              "applied": manual_result["applied"],
              "skipped": pipeline_result["skipped"] + manual_result["skipped"],
              "system_field_writebacks": writeback_count,
              "stage_date_writebacks": 0,
              "errors": pipeline_result["errors"] + manual_result["errors"] + writeback_errors}
    database.set_setting("lark_channel_last_sync", synced_at)
    return result
