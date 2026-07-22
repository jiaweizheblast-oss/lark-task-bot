"""Shared Channel Analytics table application service.

Both the website XLSX upload and the Lark/Bot submission path normalize into
one row per (report date, manual channel code, job). This module deliberately
does not write Talent Discovery candidate state.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import unicodedata
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

import channel_pipeline_schema as pipeline_schema


INDIA_TIMEZONE = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def _job_title_map(jobs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    candidates = {}
    for job in jobs:
        for title in (job.get("title"), *(job.get("title_aliases") or [])):
            key = _job_title_key(title)
            if key:
                candidates.setdefault(key, set()).add(job.get("id"))
    # A display title must resolve to exactly one stable requisition.  Keeping
    # ambiguous titles out of the map makes the importer fail closed instead
    # of silently attributing a row to whichever job happened to be last.
    return {
        key: next(iter(job_ids))
        for key, job_ids in candidates.items()
        if len(job_ids) == 1
    }


def _records_fingerprint(records: Sequence[Mapping[str, Any]]) -> str:
    canonical = json.dumps(
        sorted(
            ({"record_id": _text(row.get("record_id")),
              "last_modified_time": _text(row.get("last_modified_time")),
              "fields": dict(row.get("fields") or {})} for row in records),
            key=lambda row: row["record_id"],
        ),
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _job_title_key(value: Any) -> str:
    """Normalize a human-visible requisition title for catalog lookup only."""
    normalized = unicodedata.normalize("NFKC", _text(value))
    return " ".join(normalized.split()).casefold()


def _integer(value: Any) -> int:
    text = _text(value)
    if text == "":
        return 0
    number = int(float(text))
    if number < 0:
        raise ValueError("Counts cannot be negative")
    return number


def _valid_date(value: str) -> bool:
    try:
        datetime.date.fromisoformat(value[:10])
        return True
    except (TypeError, ValueError):
        return False


def _valid_optional_https_url(value: str) -> bool:
    if not value:
        return True
    if len(value) > 2000:
        return False
    parsed = urlsplit(value)
    return (
        parsed.scheme.casefold() == "https"
        and bool(parsed.hostname)
        and not parsed.username
        and not parsed.password
    )


def _lark_date(value: Any, default_date: str) -> str:
    """Normalize either an ISO date or Lark's millisecond Date value."""
    text = _text(value)
    if not text:
        return default_date
    if text.isdigit() and len(text) >= 10:
        seconds = int(text) / (1000 if len(text) >= 13 else 1)
        return datetime.datetime.fromtimestamp(
            seconds, tz=INDIA_TIMEZONE).date().isoformat()
    return text[:10]


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
            errors.append("Import failed (%s/%s): %s" %
                          (row.get("channel"), row.get("record_date"), error))
    return {"ok": True, "applied": applied, "imported": applied, "updated": 0,
            "skipped": int(parsed.get("skipped") or 0), "errors": errors}


def import_pipeline_rows(
    database, parsed: Mapping[str, Any], *, owner: str = "", default_date: str = "",
) -> dict[str, Any]:
    """Apply website Pipeline workbook rows without guessing candidate identity."""
    created = updated = 0
    errors = list(parsed.get("errors") or [])
    if parsed.get("fatal"):
        return {"ok": False, "created": 0, "imported": 0, "updated": 0,
                "skipped": int(parsed.get("skipped") or 0), "errors": errors}
    for index, row in enumerate(parsed.get("rows") or [], start=2):
        try:
            name, channel = _text(row.get("name")), _text(row.get("channel"))
            detail, stage = _text(row.get("source_detail")), _text(row.get("status")) or "New Lead"
            if not name or not channel or not row.get("job_request_id"):
                raise ValueError("Candidate, Source Channel, and Job are required")
            if channel == "Other" and not detail:
                raise ValueError("Other Source Details is required when Source Channel is Other")
            if channel != "Other" and detail:
                raise ValueError(
                    "Only rows with Source Channel = Other may contain Other Source Detail"
                )
            # The workbook generation date is not candidate data. Entry Date is
            # assigned at the first accepted import and never updated by HR.
            record_date = _text(default_date)[:10]
            if not _valid_date(record_date):
                raise ValueError("The system Entry Date must use YYYY-MM-DD")
            if hasattr(database, "apply_candidate_application_command"):
                artifact_id = _text(row.get("artifact_id") or parsed.get("artifact_id"))
                row_ref = _text(row.get("row_ref"))
                command_payload = {
                    "artifact_id": artifact_id,
                    "row_ref": row_ref,
                    "application_ref": _text(row.get("cand_id")),
                    "expected_version": int(row.get("expected_version") or 0),
                    "name": name,
                    "channel": channel,
                    "source_detail": detail,
                    "job_ref": _text(row.get("job_ref")),
                    "job_request_id": row.get("job_request_id"),
                    "stage": stage,
                    "note": _text(row.get("note")),
                    "hr_owner": owner or _text(row.get("filled_by")),
                    "rejection_reason": _text(row.get("rejection_reason")),
                }
                canonical = json.dumps(
                    command_payload, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                payload_sha = hashlib.sha256(canonical).hexdigest()
                event_ref = "xlsx-row-" + hashlib.sha256(
                    (artifact_id + "\n" + row_ref).encode("utf-8")
                ).hexdigest()
                result = database.apply_candidate_application_command(
                    event_id=event_ref, artifact_id=artifact_id, row_ref=row_ref,
                    payload_sha256=payload_sha, transport="xlsx",
                    entry_date=record_date, name=name, channel=channel,
                    source_detail=detail, job_request_id=row.get("job_request_id"),
                    stage=stage, note=_text(row.get("note")),
                    hr_owner=owner or _text(row.get("filled_by")),
                    rejection_reason=_text(row.get("rejection_reason")),
                    application_ref=_text(row.get("cand_id")),
                    expected_version=int(row.get("expected_version") or 0),
                    source="Excel", changed_by=owner or _text(row.get("filled_by")),
                )
                if result.get("created") and not result.get("idempotent"):
                    created += 1
                elif result.get("updated") and not result.get("idempotent"):
                    updated += 1
                continue
            candidate_id_text = _text(row.get("cand_id"))
            application = None
            if candidate_id_text.startswith("APP-") and hasattr(database, "get_candidate_application_by_ref"):
                application = database.get_candidate_application_by_ref(candidate_id_text)
            existing = (database.get_candidate(int(candidate_id_text))
                        if candidate_id_text.isdigit() else None)
            if candidate_id_text and application is None and existing is None:
                raise ValueError("System ID does not exist; identity will not be guessed from a name")
            row_ref = _text(row.get("row_ref"))
            if (not application and not existing and row_ref
                    and not hasattr(database, "create_candidate_application")):
                existing = database.get_candidate_by_ext_ref(row_ref)
            if not application and not existing and not row_ref:
                raise ValueError("A new candidate is missing Row Ref; use a system-generated workbook")
            created_now = application is None and existing is None
            if application:
                application_ref = application["application_ref"]
                if _text(application.get("name")) and name != _text(application.get("name")):
                    raise ValueError("Candidate is a protected identity field for an existing record")
                database.update_candidate_application(
                    application_ref, channel=channel, source_detail=detail,
                    job_request_id=row.get("job_request_id"), note=_text(row.get("note")),
                    hr_owner=owner or _text(row.get("filled_by")))
                current_stage = _text(application.get("current_stage")) or "New Lead"
                updated += 1
            elif existing:
                candidate_id = existing["id"]
                if _text(existing.get("name")) and name != _text(existing.get("name")):
                    raise ValueError("Candidate is a protected identity field for an existing record")
                database.update_candidate(candidate_id,
                    channel=channel, source_detail=detail, job_request_id=row.get("job_request_id"),
                    note=_text(row.get("note")), filled_by=owner or _text(row.get("filled_by")))
                updated += 1
                application_ref = ""
                current_stage = _text(existing.get("status")) or "New Lead"
            else:
                if hasattr(database, "create_candidate_application"):
                    application, inserted = database.create_candidate_application(
                        record_date, name, channel, row.get("job_request_id"),
                        _text(row.get("note")), owner or _text(row.get("filled_by")),
                        "Excel", row_ref, "", detail)
                    application_ref = application["application_ref"]
                    candidate_id = application["candidate_id"]
                    current_stage = _text(application.get("current_stage")) or "New Lead"
                    created_now = inserted
                    if inserted:
                        created += 1
                    else:
                        updated += 1
                else:
                    candidate_id = database.create_candidate(record_date, name, channel,
                        row.get("job_request_id"), "New Lead", _text(row.get("note")),
                        owner or _text(row.get("filled_by")), "Excel", row_ref, "", detail)
                    application_ref = ""
                    current_stage = "New Lead"
                    created += 1
            if created_now or stage != current_stage:
                stage_date = default_date or record_date
                reason = _text(row.get("rejection_reason"))
                identity = application_ref or str(candidate_id)
                event_material = "|".join(("xlsx", identity, stage, stage_date, reason))
                event_ref = "xlsx-" + hashlib.sha256(event_material.encode("utf-8")).hexdigest()[:32]
                if application_ref and hasattr(database, "transition_candidate_application"):
                    database.transition_candidate_application(
                        application_ref, stage, stage_date,
                        owner or _text(row.get("filled_by")), reason,
                        _text(row.get("note")), event_ref)
                else:
                    database.transition_candidate_stage(candidate_id, stage, stage_date,
                        owner or _text(row.get("filled_by")), reason, _text(row.get("note")), event_ref)
        except Exception as error:
            errors.append("Pipeline row %d: %s" % (index, error))
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
    title_to_id = _job_title_map(jobs)
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
        date_value = _lark_date(_field(fields, "record_date", manual=True), default_date)
        if not _valid_date(date_value):
            errors.append("Lark row %d: Date must use YYYY-MM-DD" % index)
            continue
        if channel not in channel_set:
            errors.append("Lark row %d: Source Channel '%s' is not an approved option" % (index, channel))
            continue
        if channel == "Other" and not source_detail:
            errors.append("Lark row %d: Other Source Details is required when Source Channel is Other" % index)
            continue
        if channel != "Other" and source_detail:
            errors.append(
                "Lark row %d: Other Source Detail is allowed only when Source Channel = Other"
                % index
            )
            continue
        job_id = title_to_id.get(_job_title_key(job_title))
        if not job_id:
            errors.append("Lark row %d: Job '%s' does not exist or is not Open" % (index, job_title))
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
            errors.append("Lark row %d: invalid count (%s)" % (index, error))
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
    """Import the single daily recruiting table without guessing identity.

    New Lark rows may omit Candidate URL (external HR intake). Existing rows
    are bound by Lark record id; candidate identity, source and requisition are
    immutable. Blank Status means the internal ``Pending`` state.
    """
    jobs_by_id = {job.get("id"): job for job in jobs}
    title_to_ids = {}
    for job in jobs:
        for title in (job.get("title"), *(job.get("title_aliases") or [])):
            key = _job_title_key(title)
            if key:
                # A rename stores both the old and current title as aliases.
                # The current title can therefore occur more than once for the
                # same job; deduplicate by stable job ID before deciding
                # whether a title is ambiguous.
                title_to_ids.setdefault(key, set()).add(job.get("id"))
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
        job_title = _text(_field(fields, "job"))
        candidate_url = _text(_field(fields, "candidate_url"))
        stage = _text(_field(fields, "status")) or "Pending"
        cv_url = _text(_field(fields, "cv_url"))
        assigned_hr = " ".join(_text(_field(fields, "filled_by")).split())
        record_id = _text(record.get("record_id"))
        application = (database.get_candidate_application_by_lark(record_id)
                       if hasattr(database, "get_candidate_application_by_lark") else None)
        existing = (None if application else database.get_candidate_by_lark(record_id))
        if application:
            job_id = application.get("job_request_id")
            signed_job_ids = title_to_ids.get(_job_title_key(job_title)) or set()
            if job_id not in signed_job_ids:
                errors.append("Pipeline row %d: Job is protected for an existing application" % index)
                continue
        else:
            open_ids = [
                job_id for job_id in (title_to_ids.get(_job_title_key(job_title)) or set())
                if _text((jobs_by_id.get(job_id) or {}).get("status") or "open").casefold() == "open"
            ]
            job_id = open_ids[0] if len(open_ids) == 1 else None
        # The record creation time is the closest server-owned source-received
        # instant available for a new Lark row. Delayed submission must not turn
        # into a false "new today" date.
        entry_date = _lark_date(record.get("created_time"), _text(default_date)[:10])
        if not name:
            errors.append("Recruiting row %d: Candidate Name is required" % index); continue
        if len(name) > 300:
            errors.append("Recruiting row %d: Candidate Name is too long" % index); continue
        if not assigned_hr or len(assigned_hr) > 80:
            errors.append("Recruiting row %d: Assigned HR is required" % index); continue
        if not _valid_optional_https_url(candidate_url):
            errors.append("Recruiting row %d: Candidate URL must be a valid HTTPS URL" % index); continue
        if not _valid_optional_https_url(cv_url):
            errors.append("Recruiting row %d: CV must be a valid HTTPS URL" % index); continue
        if len(detail) > 200:
            errors.append("Recruiting row %d: Other Source Details is too long" % index); continue
        if application:
            if (channel != _text(application.get("channel")) or
                    detail != _text(application.get("source_detail"))):
                errors.append("Recruiting row %d: Source attribution is protected for an existing application" % index); continue
        elif channel not in channel_set:
            errors.append("Recruiting row %d: Source Channel is not an approved option" % index); continue
        if channel == "Other" and not detail:
            errors.append("Recruiting row %d: Other Source Details is required when Source Channel is Other" % index); continue
        if channel != "Other" and detail:
            errors.append(
                "Recruiting row %d: Other Source Details is allowed only when Source Channel = Other"
                % index
            ); continue
        if not job_id:
            errors.append("Recruiting row %d: Hiring Job does not exist or is not Open" % index); continue
        if stage not in stage_set:
            errors.append("Recruiting row %d: Status is not an approved option" % index); continue
        if not _valid_date(entry_date):
            errors.append("Recruiting row %d: system Date is invalid" % index); continue
        try:
            created_now = application is None and existing is None
            if created_now and (jobs_by_id.get(job_id) or {}).get("status", "open") != "open":
                raise ValueError("New candidates require an Open requisition")
            if hasattr(database, "apply_candidate_application_command"):
                command_payload = {
                    "record_id": record_id,
                    "last_modified_time": _text(record.get("last_modified_time")),
                    "name": name, "candidate_url": candidate_url,
                    "channel": channel, "source_detail": detail,
                    "job_request_id": job_id, "stage": stage,
                    "hr_owner": assigned_hr,
                    "cv_url": cv_url,
                }
                canonical = json.dumps(
                    command_payload, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                payload_sha = hashlib.sha256(canonical).hexdigest()
                lark_revision = _text(record.get("last_modified_time")) or payload_sha
                event_id = "lark-row-" + hashlib.sha256(
                    (record_id + "\n" + lark_revision).encode("utf-8")
                ).hexdigest()
                result = database.apply_candidate_application_command(
                    event_id=event_id, artifact_id="lark-live-pipeline",
                    row_ref=record_id, payload_sha256=payload_sha, transport="lark",
                    entry_date=entry_date, name=name, channel=channel,
                    source_detail=detail, job_request_id=job_id, stage=stage,
                    note="",
                    hr_owner=assigned_hr,
                    rejection_reason="",
                    application_ref=_text((application or {}).get("application_ref")),
                    expected_version=int((application or {}).get("record_version") or 0),
                    lark_record_id=record_id, source="Lark",
                    changed_by=assigned_hr,
                    baseline_import=False, candidate_url=candidate_url, cv_url=cv_url,
                    stage_effective_date=default_date,
                )
                if result.get("created") and not result.get("idempotent"):
                    created += 1
                elif result.get("updated") and not result.get("idempotent"):
                    updated += 1
                continue
            if application:
                application_ref = application["application_ref"]
                candidate_id = application["candidate_id"]
                if _text(application.get("name")) and name != _text(application.get("name")):
                    raise ValueError("Candidate is a protected identity field for an existing record")
                database.update_candidate_application(
                    application_ref, channel=channel, source_detail=detail,
                    job_request_id=job_id, note=_text(_field(fields, "note")),
                    hr_owner=_text(_field(fields, "filled_by")),
                    lark_record_id=record_id)
                current_stage = _text(application.get("current_stage")) or "Pending"
                updated += 1
            elif existing:
                candidate_id = existing["id"]
                if _text(existing.get("name")) and name != _text(existing.get("name")):
                    raise ValueError("Candidate is a protected identity field for an existing record")
                database.update_candidate(candidate_id,
                    channel=channel, source_detail=detail, job_request_id=job_id,
                    note=_text(_field(fields, "note")), filled_by=_text(_field(fields, "filled_by")),
                    lark_record_id=record_id)
                updated += 1
                application_ref = ""
                current_stage = _text(existing.get("status")) or "Pending"
            else:
                if hasattr(database, "create_candidate_application"):
                    application, inserted = database.create_candidate_application(
                        entry_date, name, channel, job_id, _text(_field(fields, "note")),
                        _text(_field(fields, "filled_by")), "Lark", "", record_id, detail)
                    application_ref = application["application_ref"]
                    candidate_id = application["candidate_id"]
                    current_stage = _text(application.get("current_stage")) or "Pending"
                    created_now = inserted
                    if inserted:
                        created += 1
                    else:
                        updated += 1
                else:
                    candidate_id = database.create_candidate(
                        entry_date, name, channel, job_id, "Pending", "",
                        _text(_field(fields, "filled_by")), "Lark", "", record_id, detail)
                    application_ref = ""
                    current_stage = "Pending"
                    created += 1
            if created_now or stage != current_stage:
                # Stage Started On is system-owned. The database records the
                # canonical Kolkata processing date and never accepts a value
                # supplied by HR, Excel, or Lark.
                stage_date = default_date
                identity = application_ref or str(candidate_id)
                event_material = "|".join((record_id, identity, stage, stage_date))
                event_ref = "lark-" + hashlib.sha256(event_material.encode("utf-8")).hexdigest()[:32]
                if application_ref and hasattr(database, "transition_candidate_application"):
                    database.transition_candidate_application(
                        application_ref, stage, stage_date,
                        _text(_field(fields, "filled_by")), "", "", event_ref)
                else:
                    database.transition_candidate_stage(candidate_id, stage, stage_date,
                        _text(_field(fields, "filled_by")), "", "", event_ref)
        except Exception as error:
            errors.append("Recruiting row %d: %s" % (index, error))
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
    if (not app_token or not pipeline_table_id
            or config.get("schema_version") != pipeline_schema.SCHEMA_VERSION):
        return {"ok": False, "error": "Today's Recruiting table is not configured"}
    pipeline_response = lark_client.list_pipeline_records(app_token, pipeline_table_id)
    if not pipeline_response.get("ok"):
        return pipeline_response
    # Optimistic read barrier: submit only a stable snapshot. If HR edits any
    # row while the service is reading, nothing is written and the manager can
    # safely retry. This also protects the simple Bot /submit command from
    # importing an in-flight workbook.
    if getattr(lark_client, "supports_revision_barrier", False):
        pipeline_check = lark_client.list_pipeline_records(app_token, pipeline_table_id)
        if not pipeline_check.get("ok"):
            return {"ok": False, "error": "Unable to re-read Lark before commit"}
        if (_records_fingerprint(pipeline_response.get("records") or []) !=
                _records_fingerprint(pipeline_check.get("records") or [])):
            return {"ok": False, "error": "Lark rows changed during submission; no data was imported. Please retry."}
    pipeline_result = import_lark_pipeline_records(
        database, pipeline_response.get("records") or [], jobs=jobs, channels=channels,
        stages=("Pending", "Contacted / Awaiting Reply", "HR Screening", "Interview",
                "Offer", "Hired", "Rejected", "Withdrawn", "Resigned"),
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
            writeback_errors.append(response.get("error") or "System identity writeback failed")
    all_errors = pipeline_result["errors"] + writeback_errors
    result = {"ok": True, "pipeline": pipeline_result,
              "created": pipeline_result["created"], "updated": pipeline_result["updated"],
              "applied": 0, "skipped": pipeline_result["skipped"],
              "system_field_writebacks": writeback_count,
              "stage_date_writebacks": 0,
              "errors": all_errors,
              "status": "partially_imported" if all_errors else "synchronized"}
    database.set_setting("lark_channel_last_attempt", synced_at)
    if not all_errors:
        database.set_setting("lark_channel_last_sync", synced_at)
    return result
