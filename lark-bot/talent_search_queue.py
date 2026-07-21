import hashlib
import json
import math
import re
import uuid
from datetime import datetime, timedelta, timezone


SCHEMA_VERSION = "talent-search-task-v1"
TASK_TYPE = "preview_search"
RESULT_SCHEMA_VERSION = "talent-search-result-v1"
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


def build_task(command, *, now=None):
    if not isinstance(command, dict):
        raise ValueError("task command must be an object")
    allowed = {
        "task_id", "core_job_ref", "requested_contact_count",
        "max_review_pool_count", "budgets",
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
    requested = _integer(
        command.get("requested_contact_count"),
        "requested_contact_count", 1, 200,
    )
    max_review = _integer(
        command.get("max_review_pool_count", 10),
        "max_review_pool_count", 0, 50,
    )
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
        "core_job_ref": core_job_ref,
        "requested_contact_count": requested,
        "max_review_pool_count": max_review,
        "budgets": budgets,
    }
    task["payload_sha256"] = sha256(task)
    return task


def validate_result(value, *, task_id, core_job_ref):
    if not isinstance(value, dict):
        raise ValueError("result must be an object")
    if len(canonical_json(value)) > 800_000:
        raise ValueError("result exceeds 800 KB")
    if value.get("schema_version") != RESULT_SCHEMA_VERSION:
        raise ValueError("unsupported result schema")
    if value.get("task_id") != task_id or value.get("core_job_ref") != core_job_ref:
        raise ValueError("result identity does not match task")
    if value.get("database_changed") is not False:
        raise ValueError("preview result indicates a database write")
    for field in (
        "daily_batches_created", "review_tasks_created", "lark_calls",
        "contactout_calls", "linkedin_profile_pages_opened",
    ):
        if value.get(field) != 0:
            raise ValueError(f"preview result violates {field} boundary")
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
