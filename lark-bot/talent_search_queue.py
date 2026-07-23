import hashlib
import json
import math
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit


SCHEMA_VERSION = "talent-search-task-v3"
TASK_TYPE = "preview_search"
RESULT_SCHEMA_VERSION = "talent-search-result-v1"
PUBLICATION_SCHEMA_VERSION = "talent-daily-publication-task-v3"
PUBLICATION_TASK_TYPE = "apply_and_publish_daily_recruiting_workbook"
PUBLICATION_RECEIPT_SCHEMA_VERSION = "talent-daily-publication-receipt-v2"
TERMINAL_STATUSES = {"succeeded", "shortfall", "failed", "cancelled"}
JOB_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
WORKER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


DEFAULT_BUDGETS = {
    "max_sources": 2,
    "max_queries_per_source": 12,
    "max_pages_per_query": 2,
    "max_results_per_source": 250,
    "max_total_observations": 500,
    "max_enrichment_candidates": 50,
    "max_provider_api_requests": 20,
    "max_enrichment_api_requests": 10,
    "time_budget_seconds": 600.0,
    "api_time_budget_seconds": 90.0,
    "request_timeout_seconds": 10.0,
}
INTEGER_BOUNDS = {
    "max_sources": (1, 4),
    "max_queries_per_source": (1, 30),
    "max_pages_per_query": (1, 5),
    "max_results_per_source": (1, 1000),
    "max_total_observations": (1, 2000),
    "max_enrichment_candidates": (0, 200),
    "max_provider_api_requests": (0, 100),
    "max_enrichment_api_requests": (0, 100),
}
NUMBER_BOUNDS = {
    "time_budget_seconds": (30, 1800),
    "api_time_budget_seconds": (10, 300),
    "request_timeout_seconds": (2, 30),
}


def canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _instant(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def sha256(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _integer(value, field, low, high):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    if value < low or value > high:
        raise ValueError(f"{field} must be between {low} and {high}")
    return value


def _number(value, field, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < low or result > high:
        raise ValueError(f"{field} must be between {low} and {high}")
    return result


def normalize_budgets(value):
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("budgets must be an object")
    unknown = set(value) - set(DEFAULT_BUDGETS)
    if unknown:
        raise ValueError("unknown budget fields")
    result = {}
    for field, default in DEFAULT_BUDGETS.items():
        raw = value.get(field, default)
        if field in INTEGER_BOUNDS:
            result[field] = _integer(raw, field, *INTEGER_BOUNDS[field])
        else:
            result[field] = _number(raw, field, *NUMBER_BOUNDS[field])
    return result


def normalize_hr_allocations(value):
    """Validate the small manager-authored HR split embedded in the task.

    Allocation is part of the signed search contract so a completed search
    cannot later be assigned using a different roster or quota split.
    """
    if not isinstance(value, list) or not value or len(value) > 20:
        raise ValueError("hr_allocations must contain 1 to 20 HR rows")
    result, names = [], set()
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict) or set(item) != {"name", "count"}:
            raise ValueError("each HR allocation must contain only name and count")
        name = " ".join(str(item.get("name") or "").split())
        if not name or len(name) > 80:
            raise ValueError(f"HR allocation {index} has an invalid name")
        key = name.casefold()
        if key in names:
            raise ValueError("HR names must be unique")
        names.add(key)
        result.append({
            "name": name,
            "count": _integer(item.get("count"), f"hr_allocations[{index}].count", 1, 200),
        })
    return result


def normalize_hr_names(value):
    if not isinstance(value, list) or not value or len(value) > 20:
        raise ValueError("hr_names must contain 1 to 20 HR names")
    result, seen = [], set()
    for index, raw in enumerate(value, start=1):
        name = " ".join(str(raw or "").split())
        if not name or len(name) > 80 or name.isdecimal():
            raise ValueError(f"hr_names[{index}] is invalid")
        key = name.casefold()
        if key in seen:
            raise ValueError("HR names must be unique")
        seen.add(key)
        result.append(name)
    return result


def normalize_open_jobs(value):
    if not isinstance(value, list) or not value:
        raise ValueError("open_jobs cannot be empty")
    result, refs, labels = [], set(), set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {
            "operational_job_ref", "hiring_job_label",
        }:
            raise ValueError("open_jobs fields are invalid")
        job_ref = str(item.get("operational_job_ref") or "").strip()
        label = " ".join(str(item.get("hiring_job_label") or "").split())
        if not JOB_REF.fullmatch(job_ref) or not label or len(label) > 255:
            raise ValueError("open_jobs contains an invalid Job Requisition")
        if job_ref in refs or label.casefold() in labels:
            raise ValueError("open_jobs must have unique references and labels")
        refs.add(job_ref)
        labels.add(label.casefold())
        result.append({
            "operational_job_ref": job_ref,
            "hiring_job_label": label,
        })
    return result


def build_task(command, *, now=None):
    if not isinstance(command, dict):
        raise ValueError("task command must be an object")
    allowed = {
        "task_id", "operational_job_ref", "core_job_ref", "requested_contact_count",
        "max_review_pool_count", "hr_allocations", "budgets", "auto_publish",
    }
    if set(command) - allowed:
        raise ValueError("task command contains unknown fields")
    try:
        task_id = str(uuid.UUID(str(command.get("task_id") or uuid.uuid4())))
    except ValueError as error:
        raise ValueError("task_id must be a UUID") from error
    core_job_ref = str(command.get("core_job_ref") or "").strip()
    if not JOB_REF.fullmatch(core_job_ref):
        raise ValueError("core_job_ref is invalid")
    operational_job_ref = str(command.get("operational_job_ref") or "").strip()
    if not JOB_REF.fullmatch(operational_job_ref):
        raise ValueError("operational_job_ref is invalid")
    hr_allocations = normalize_hr_allocations(command.get("hr_allocations"))
    allocated_total = sum(item["count"] for item in hr_allocations)
    requested = _integer(
        command.get("requested_contact_count", allocated_total),
        "requested_contact_count", 1, 200,
    )
    if requested != allocated_total:
        raise ValueError(
            "requested_contact_count must equal the HR allocation total"
        )
    max_review = _integer(
        command.get("max_review_pool_count", 0),
        "max_review_pool_count", 0, 50,
    )
    auto_publish = command.get("auto_publish", False)
    if not isinstance(auto_publish, bool):
        raise ValueError("auto_publish must be a boolean")
    budgets = normalize_budgets(command.get("budgets"))
    if budgets["max_total_observations"] < requested:
        raise ValueError("max_total_observations is below the requested quota")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    task = {
        "schema_version": SCHEMA_VERSION,
        "task_id": task_id,
        "task_type": TASK_TYPE,
        "revision": 1,
        "created_at": current.isoformat(),
        "expires_at": (current + timedelta(hours=24)).isoformat(),
        "operational_job_ref": operational_job_ref,
        "core_job_ref": core_job_ref,
        "requested_contact_count": requested,
        "max_review_pool_count": max_review,
        "auto_publish": auto_publish,
        "hr_allocations": hr_allocations,
        "budgets": budgets,
    }
    task["payload_sha256"] = sha256(task)
    return task


def validate_result(
    value, *, task_id, operational_job_ref, core_job_ref, hr_allocations=None,
):
    if not isinstance(value, dict):
        raise ValueError("result must be an object")
    if len(canonical_json(value)) > 800_000:
        raise ValueError("result exceeds 800 KB")
    if value.get("schema_version") != RESULT_SCHEMA_VERSION:
        raise ValueError("unsupported result schema")
    if (value.get("task_id") != task_id
            or value.get("operational_job_ref") != operational_job_ref
            or value.get("core_job_ref") != core_job_ref):
        raise ValueError("result identity does not match task")
    if hr_allocations is not None and value.get("hr_allocations") != hr_allocations:
        raise ValueError("result HR allocation does not match task")
    requested = sum(
        int(item.get("count") or 0) for item in (hr_allocations or [])
        if isinstance(item, dict)
    )
    if "publication_manifest" in value:
        raise ValueError(
            "preview results cannot contain directly publishable candidate rows"
        )
    frozen = value.get("frozen_bundle")
    fulfilled = value.get("quota_fulfilled") is True and value.get("applicable") is True
    if fulfilled:
        if not isinstance(frozen, dict) or set(frozen) != {
            "plan_id", "plan_sha256", "expires_at", "stored_on_worker",
        }:
            raise ValueError("fulfilled result must contain one frozen-plan receipt")
        try:
            uuid.UUID(str(frozen.get("plan_id")))
        except ValueError as error:
            raise ValueError("frozen plan_id is invalid") from error
        plan_sha = str(frozen.get("plan_sha256") or "").casefold()
        if len(plan_sha) != 64 or any(char not in "0123456789abcdef" for char in plan_sha):
            raise ValueError("frozen plan_sha256 is invalid")
        expires_at = _instant(frozen.get("expires_at"), "frozen expires_at")
        if expires_at <= datetime.now(timezone.utc):
            raise ValueError("frozen plan has expired")
        if frozen.get("stored_on_worker") is not True:
            raise ValueError("frozen plan is not retained by the local worker")
        if value.get("selected_contacts") != requested:
            raise ValueError("selected contact count does not match HR allocation")
    elif frozen is not None:
        raise ValueError("a non-applicable result cannot retain a publishable frozen plan")
    if value.get("database_changed") is not False:
        raise ValueError("preview result indicates a database write")
    for field in (
        "daily_batches_created", "review_tasks_created", "lark_calls",
        "contactout_calls", "linkedin_profile_pages_opened",
    ):
        if value.get(field) != 0:
            raise ValueError(f"preview result violates {field} boundary")
    return value


def build_publication_cohort(row, *, hiring_job_label):
    """Build one hash-bound cohort descriptor without candidate row data."""
    if not isinstance(row, dict) or row.get("status") != "succeeded":
        raise ValueError("only a successful search can be queued for publication")
    payload = row.get("payload") or {}
    result = row.get("result") or {}
    frozen = result.get("frozen_bundle") or {}
    cohort = {
        "search_task_id": str(row["task_id"]),
        "search_task_revision": int(row.get("revision") or 1),
        "operational_job_ref": str(payload.get("operational_job_ref") or ""),
        "hiring_job_label": str(hiring_job_label or "").strip(),
        "core_job_ref": str(row.get("core_job_ref") or ""),
        "requested_contact_count": int(payload.get("requested_contact_count") or 0),
        "hr_allocations": list(payload.get("hr_allocations") or []),
        "search_task_payload_sha256": str(row.get("payload_sha256") or ""),
        "search_result_sha256": str(row.get("result_sha256") or ""),
        "frozen_plan_id": str(frozen.get("plan_id") or ""),
        "frozen_plan_sha256": str(frozen.get("plan_sha256") or ""),
        "frozen_plan_expires_at": str(frozen.get("expires_at") or ""),
    }
    if cohort["operational_job_ref"] != payload.get("operational_job_ref"):
        raise ValueError("operational job identity is invalid")
    if not JOB_REF.fullmatch(cohort["operational_job_ref"]):
        raise ValueError("operational_job_ref is invalid")
    if (
        not cohort["hiring_job_label"]
        or len(cohort["hiring_job_label"]) > 255
    ):
        raise ValueError("hiring_job_label is invalid")
    if not JOB_REF.fullmatch(cohort["core_job_ref"]):
        raise ValueError("core_job_ref is invalid")
    if cohort["requested_contact_count"] != result.get("selected_contacts"):
        raise ValueError("frozen cohort no longer matches the requested quota")
    _instant(cohort["frozen_plan_expires_at"], "frozen_plan_expires_at")
    validate_result(
        result,
        task_id=cohort["search_task_id"],
        operational_job_ref=cohort["operational_job_ref"],
        core_job_ref=cohort["core_job_ref"],
        hr_allocations=cohort["hr_allocations"],
    )
    return cohort


def build_publication_task(
    cohorts, *, publication_id, business_date, now=None, hr_names=None,
    open_jobs=None, manual_rows_per_hr=30,
):
    """Build one immutable daily workbook command.

    Search cohorts are optional.  The signed HR roster and Open Job
    Requisition catalog are sufficient to create a manual-only workbook.
    """
    if not isinstance(cohorts, list):
        raise ValueError("cohorts must be a list")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if len({item["search_task_id"] for item in cohorts}) != len(cohorts):
        raise ValueError("a search task is duplicated across publication cohorts")
    if len({item["operational_job_ref"] for item in cohorts}) != len(cohorts):
        raise ValueError("an operational requisition is duplicated across cohorts")
    if len({item["hiring_job_label"].casefold() for item in cohorts}) != len(cohorts):
        raise ValueError("hiring job labels must be unique across cohorts")
    expiries = [
        _instant(item["frozen_plan_expires_at"], "frozen_plan_expires_at")
        for item in cohorts
    ]
    expires_at = min(expiries) if expiries else current + timedelta(hours=24)
    if expires_at <= current:
        raise ValueError("one or more frozen publication cohorts have expired")
    try:
        parsed_business_date = date.fromisoformat(str(business_date))
    except ValueError as error:
        raise ValueError("business_date must be YYYY-MM-DD") from error
    if hr_names is None:
        hr_names = []
        seen_hr = set()
        for cohort in cohorts:
            for allocation in cohort.get("hr_allocations") or []:
                key = str(allocation.get("name") or "").casefold()
                if key not in seen_hr:
                    seen_hr.add(key)
                    hr_names.append(allocation.get("name"))
    if open_jobs is None:
        open_jobs = [
            {
                "operational_job_ref": item["operational_job_ref"],
                "hiring_job_label": item["hiring_job_label"],
            }
            for item in cohorts
        ]
    hr_names = normalize_hr_names(hr_names)
    open_jobs = normalize_open_jobs(open_jobs)
    manual_rows_per_hr = _integer(
        manual_rows_per_hr, "manual_rows_per_hr", 1, 500
    )
    job_catalog = {
        item["operational_job_ref"]: item["hiring_job_label"].casefold()
        for item in open_jobs
    }
    hr_catalog = {name.casefold() for name in hr_names}
    for cohort in cohorts:
        if job_catalog.get(cohort["operational_job_ref"]) != (
            cohort["hiring_job_label"].casefold()
        ):
            raise ValueError("publication cohort is outside the open-job catalog")
        if not {
            str(item.get("name") or "").casefold()
            for item in cohort.get("hr_allocations") or []
        }.issubset(hr_catalog):
            raise ValueError("publication cohort is outside the HR roster")
    command = {
        "schema_version": PUBLICATION_SCHEMA_VERSION,
        "task_type": PUBLICATION_TASK_TYPE,
        "publication_id": str(publication_id),
        "revision": 1,
        "created_at": current.isoformat(),
        "expires_at": expires_at.isoformat(),
        "business_date": parsed_business_date.isoformat(),
        "hr_names": hr_names,
        "open_jobs": open_jobs,
        "manual_rows_per_hr": manual_rows_per_hr,
        "cohorts": cohorts,
        "total_contact_count": sum(
            int(item["requested_contact_count"]) for item in cohorts
        ),
        "cohort_manifest_sha256": sha256(cohorts),
    }
    try:
        uuid.UUID(command["publication_id"])
    except ValueError as error:
        raise ValueError("publication_id is invalid") from error
    command["payload_sha256"] = sha256(command)
    return command


def validate_publication_receipt(value, *, command):
    if not isinstance(value, dict):
        raise ValueError("publication receipt must be an object")
    expected = {
        "schema_version", "publication_id", "business_date", "artifact_id",
        "spreadsheet_token", "spreadsheet_url", "workbook_sha256",
        "cohort_count", "selected_contacts", "cohort_manifest_sha256",
        "database_integrity", "backup_verified", "published_at", "payload_sha256",
    }
    if set(value) != expected:
        raise ValueError("publication receipt fields are invalid")
    if value["schema_version"] != PUBLICATION_RECEIPT_SCHEMA_VERSION:
        raise ValueError("unsupported publication receipt schema")
    supplied_hash = str(value.get("payload_sha256") or "").casefold()
    unsigned = {key: value[key] for key in expected if key != "payload_sha256"}
    if supplied_hash != sha256(unsigned):
        raise ValueError("publication receipt hash does not match")
    if value["publication_id"] != command.get("publication_id"):
        raise ValueError("publication receipt identity does not match")
    if value["business_date"] != command.get("business_date"):
        raise ValueError("publication receipt business date does not match")
    try:
        uuid.UUID(str(value["artifact_id"]))
    except ValueError as error:
        raise ValueError("publication artifact_id is invalid") from error
    token = str(value.get("spreadsheet_token") or "").strip()
    if not token or len(token) > 300:
        raise ValueError("publication spreadsheet token is invalid")
    url = urlsplit(str(value.get("spreadsheet_url") or "").strip())
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or "/sheets/" not in url.path or url.query or url.fragment):
        raise ValueError("publication receipt is not a native Lark Sheet URL")
    for field in ("workbook_sha256", "cohort_manifest_sha256"):
        digest = str(value.get(field) or "").casefold()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError(f"publication {field} is invalid")
    if value["cohort_manifest_sha256"] != command.get("cohort_manifest_sha256"):
        raise ValueError("publication cohort manifest hash does not match")
    if value.get("database_integrity") != "ok" or value.get("backup_verified") is not True:
        raise ValueError("publication database verification is incomplete")
    if value.get("cohort_count") != len(command.get("cohorts") or []):
        raise ValueError("publication cohort count does not match")
    if value.get("selected_contacts") != command.get("total_contact_count"):
        raise ValueError("publication selected count does not match")
    _instant(value.get("published_at"), "published_at")
    return value


def valid_worker_id(value):
    result = str(value or "").strip()
    if not WORKER_ID.fullmatch(result):
        raise ValueError("worker_id is invalid")
    return result


def valid_lease_seconds(value):
    return _integer(value, "lease_seconds", 60, 300)


def valid_error_code(value):
    result = str(value or "").strip()
    if not ERROR_CODE.fullmatch(result):
        raise ValueError("error_code is invalid")
    return result
