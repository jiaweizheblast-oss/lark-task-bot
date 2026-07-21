"""Shared Channel Analytics table application service.

Both the website XLSX upload and the Lark/Bot submission path normalize into
one row per (report date, manual channel code, job). This module deliberately
does not write Talent Discovery candidate state.
"""
from __future__ import annotations

import datetime
import hashlib
from typing import Any, Mapping, Sequence


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


def import_pipeline_rows(database, parsed: Mapping[str, Any], *, owner: str = "") -> dict[str, Any]:
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
                raise ValueError("选择 Other 时必须填写 Source Detail")
            record_date = _text(row.get("record_date"))[:10]
            if not _valid_date(record_date):
                raise ValueError("日期必须是 YYYY-MM-DD")
            candidate_id_text = _text(row.get("cand_id"))
            existing = database.get_candidate(int(candidate_id_text)) if candidate_id_text.isdigit() else None
            if candidate_id_text and existing is None:
                raise ValueError("System ID 不存在；拒绝按姓名猜测身份")
            row_ref = _text(row.get("row_ref"))
            if not existing and row_ref:
                existing = database.get_candidate_by_ext_ref(row_ref)
            if not existing and not row_ref:
                raise ValueError("新候选人缺少系统 Row Ref；请使用系统生成的表")
            if existing:
                candidate_id = existing["id"]
                database.update_candidate(candidate_id, apply_date=record_date, name=name,
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
            if stage != current_stage:
                stage_date = _text(row.get("stage_date"))[:10] or record_date
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
        channel = _text(fields.get("招聘渠道"))
        source_detail = _text(fields.get("Source Detail"))
        job_title = _text(fields.get("关联职位"))
        counts = [fields.get("今日新增简历数"), fields.get("初筛通过数"),
                  fields.get("已推荐面试数"), fields.get("已拒绝数")]
        if not channel and not job_title and not any(_text(value) for value in counts):
            skipped += 1
            continue
        date_value = _text(fields.get("日期"))[:10] or default_date
        if not _valid_date(date_value):
            errors.append("Lark 第%d条：日期格式非法（应为 YYYY-MM-DD）" % index)
            continue
        if channel not in channel_set:
            errors.append("Lark 第%d条：渠道「%s」不在受控词表" % (index, channel))
            continue
        if channel == "Other" and not source_detail:
            errors.append("Lark 第%d条：选择 Other 时必须填写 Source Detail" % index)
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
                "filled_by": _text(fields.get("填写人")),
                "new_resumes": _integer(fields.get("今日新增简历数")),
                "passed_screening": _integer(fields.get("初筛通过数")),
                "recommended": _integer(fields.get("已推荐面试数")),
                "rejected": _integer(fields.get("已拒绝数")),
                "note": _text(fields.get("备注")),
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
    for index, record in enumerate(records, start=1):
        fields = record.get("fields") or {}
        name = _text(fields.get("Candidate"))
        if not name and not any(_text(value) for value in fields.values()):
            skipped += 1
            continue
        channel = _text(fields.get("Source Channel"))
        detail = _text(fields.get("Source Detail"))
        job_id = title_to_id.get(_text(fields.get("Job")))
        stage = _text(fields.get("Current Stage")) or "New Lead"
        entry_date = _text(fields.get("Entry Date"))[:10] or default_date
        stage_date = _text(fields.get("Stage Date"))[:10] or entry_date
        reason = _text(fields.get("Rejection Reason"))
        if not name:
            errors.append("Pipeline 第%d条：Candidate 必填" % index); continue
        if channel not in channel_set:
            errors.append("Pipeline 第%d条：Source Channel 非法" % index); continue
        if channel == "Other" and not detail:
            errors.append("Pipeline 第%d条：选择 Other 时必须填写 Source Detail" % index); continue
        if not job_id:
            errors.append("Pipeline 第%d条：Job 不存在或已停用" % index); continue
        if stage not in stage_set:
            errors.append("Pipeline 第%d条：Current Stage 非法" % index); continue
        if not _valid_date(entry_date) or not _valid_date(stage_date):
            errors.append("Pipeline 第%d条：日期必须是 YYYY-MM-DD" % index); continue
        if stage == "Rejected" and not reason:
            errors.append("Pipeline 第%d条：Rejected 必须填写 Rejection Reason" % index); continue
        record_id = _text(record.get("record_id"))
        try:
            existing = database.get_candidate_by_lark(record_id)
            system_id = _text(fields.get("System ID"))
            if not existing and system_id.isdigit():
                existing = database.get_candidate(int(system_id))
            if existing:
                candidate_id = existing["id"]
                database.update_candidate(candidate_id, apply_date=entry_date, name=name,
                    channel=channel, source_detail=detail, job_request_id=job_id,
                    note=_text(fields.get("Note")), filled_by=_text(fields.get("HR Owner")),
                    lark_record_id=record_id)
                updated += 1
            else:
                candidate_id = database.create_candidate(
                    entry_date, name, channel, job_id, "New Lead", _text(fields.get("Note")),
                    _text(fields.get("HR Owner")), "Lark", "", record_id, detail)
                existing = {"status": "New Lead"}
                created += 1
            current_stage = _text(existing.get("status")) or "New Lead"
            if stage != current_stage:
                event_material = "|".join((record_id, str(candidate_id), stage, stage_date, reason))
                event_ref = "lark-" + hashlib.sha256(event_material.encode("utf-8")).hexdigest()[:32]
                database.transition_candidate_stage(candidate_id, stage, stage_date,
                    _text(fields.get("HR Owner")), reason, _text(fields.get("Note")), event_ref)
        except Exception as error:
            errors.append("Pipeline 第%d条：%s" % (index, error))
    return {"ok": True, "created": created, "updated": updated, "skipped": skipped, "errors": errors}


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
    manual_result = import_lark_channel_records(
        database, manual_response.get("records") or [], jobs=jobs,
        default_date=default_date, channels=channels)
    result = {"ok": True, "pipeline": pipeline_result, "manual": manual_result,
              "created": pipeline_result["created"], "updated": pipeline_result["updated"],
              "applied": manual_result["applied"],
              "skipped": pipeline_result["skipped"] + manual_result["skipped"],
              "errors": pipeline_result["errors"] + manual_result["errors"]}
    database.set_setting("lark_channel_last_sync", synced_at)
    return result
