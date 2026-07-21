import copy
from datetime import datetime, timezone

import talent_integration


KEY = "nexus-recruiting-test-key-at-least-32-bytes"
GOLDEN = {
    "schema_version": "nexus-recruiting-snapshot-v1",
    "source_system": "ai-talent-discovery",
    "generated_at": "2026-07-21T10:30:00+00:00",
    "content_sha256": "75e54827876e74bca0845eb61b1af808b9e483b5716394bef2a54dc171d3e3b2",
    "content": {
        "visibility": "manager_only",
        "timezone_name": "Asia/Kolkata",
        "metrics": {"candidate_count": 1},
        "jobs": [{"core_job_ref": "job-001", "title": "Sales"}],
        "candidate_matches": [{
            "candidate_ref": "candidate-001",
            "match_ref": "match-001",
            "core_job_ref": "job-001",
            "score": "72",
            "score_visibility": "manager_only",
        }],
    },
    "signature": "3dea3ea4c932fc6d093b9f291f90cff291fa1c376f7a07bb69f655da3149e439",
}


def rejected(value, message):
    try:
        talent_integration.verify_snapshot(
            value,
            KEY,
            now=datetime(2026, 7, 21, 10, 31, tzinfo=timezone.utc),
        )
    except ValueError:
        return
    raise AssertionError(message)


def main():
    verified = talent_integration.verify_snapshot(
        copy.deepcopy(GOLDEN),
        KEY,
        now=datetime(2026, 7, 21, 10, 31, tzinfo=timezone.utc),
    )
    assert verified["signature"] == GOLDEN["signature"]

    changed = copy.deepcopy(GOLDEN)
    changed["content"]["candidate_matches"][0]["score"] = "99"
    rejected(changed, "Tampered score was accepted")

    wrong_visibility = copy.deepcopy(GOLDEN)
    wrong_visibility["content"]["candidate_matches"][0]["score_visibility"] = "hr"
    rejected(wrong_visibility, "HR-visible score was accepted")

    expired = copy.deepcopy(GOLDEN)
    try:
        talent_integration.verify_snapshot(
            expired,
            KEY,
            now=datetime(2026, 7, 23, 10, 31, tzinfo=timezone.utc),
        )
    except ValueError as exc:
        assert "expired" in str(exc)
    else:
        raise AssertionError("Expired snapshot was accepted")

    print("AI-TD/Nexus golden snapshot: PASSED")
    print("Tampering, HR-visible scores, and expired mirrors: REJECTED")


if __name__ == "__main__":
    main()
