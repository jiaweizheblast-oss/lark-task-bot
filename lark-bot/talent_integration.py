"""Fail-closed verification for AI Talent Discovery read-only mirrors."""

import hashlib
import hmac
import json
from datetime import datetime, timezone
from urllib.parse import urlsplit


SCHEMA_VERSION = "nexus-recruiting-snapshot-v1"
SOURCE_SYSTEM = "ai-talent-discovery"
MAX_BODY_BYTES = 8_000_000
MINIMUM_KEY_BYTES = 32
MAX_FUTURE_SKEW_SECONDS = 300
MAX_SNAPSHOT_AGE_SECONDS = 86_400


def _canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _key(value):
    if not isinstance(value, str) or len(value.encode("utf-8")) < MINIMUM_KEY_BYTES:
        raise ValueError("NEXUS_INTEGRATION_SIGNING_KEY is not configured safely")
    return value.encode("utf-8")


def verify_snapshot(value, signing_key, now=None):
    """Return a verified envelope; never infer or repair missing fields."""
    if not isinstance(value, dict):
        raise ValueError("snapshot envelope must be an object")
    required = {
        "schema_version", "source_system", "generated_at",
        "content_sha256", "content", "signature",
    }
    if set(value) != required:
        raise ValueError("snapshot envelope fields are invalid")
    if value["schema_version"] != SCHEMA_VERSION or value["source_system"] != SOURCE_SYSTEM:
        raise ValueError("snapshot identity is invalid")
    content = value["content"]
    if not isinstance(content, dict) or content.get("visibility") != "manager_only":
        raise ValueError("snapshot visibility is invalid")
    actual_sha = hashlib.sha256(_canonical_json(content)).hexdigest()
    if not hmac.compare_digest(str(value["content_sha256"]), actual_sha):
        raise ValueError("snapshot content hash is invalid")
    unsigned = {key: value[key] for key in required if key != "signature"}
    expected = hmac.new(_key(signing_key), _canonical_json(unsigned), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(str(value["signature"]), expected):
        raise ValueError("snapshot signature is invalid")
    try:
        generated_at = datetime.fromisoformat(str(value["generated_at"]))
    except ValueError as exc:
        raise ValueError("snapshot generated_at is invalid") from exc
    if generated_at.tzinfo is None:
        raise ValueError("snapshot generated_at needs an offset")
    current = now or datetime.now(timezone.utc)
    age_seconds = (current - generated_at.astimezone(timezone.utc)).total_seconds()
    if age_seconds < -MAX_FUTURE_SKEW_SECONDS:
        raise ValueError("snapshot is from the future")
    if age_seconds > MAX_SNAPSHOT_AGE_SECONDS:
        raise ValueError("snapshot has expired")
    if not isinstance(content.get("jobs"), list) or not isinstance(content.get("candidate_matches"), list):
        raise ValueError("snapshot collections are invalid")
    for job in content["jobs"]:
        if not isinstance(job, dict) or not str(job.get("core_job_ref") or "").strip():
            raise ValueError("snapshot job ref is invalid")
    for row in content["candidate_matches"]:
        if not isinstance(row, dict):
            raise ValueError("snapshot candidate row is invalid")
        for field in ("candidate_ref", "match_ref", "core_job_ref"):
            if not str(row.get(field) or "").strip():
                raise ValueError("snapshot candidate identity is invalid")
        if row.get("score_visibility") != "manager_only":
            raise ValueError("snapshot candidate score visibility is invalid")
        profile_url = row.get("public_profile_url")
        if profile_url:
            parsed = urlsplit(str(profile_url))
            if parsed.scheme != "https" or not parsed.hostname:
                raise ValueError("snapshot public profile URL is invalid")
    return value
