import copy
from datetime import datetime, timezone

import talent_search_queue


def main():
    command = {
        "task_id": "97871e24-6f1c-41ce-b066-dc79a60aacd4",
        "operational_job_ref": "REQ-20260722-TEST0001",
        "core_job_ref": "job-core-001",
        "requested_contact_count": 100,
        "max_review_pool_count": 10,
        "hr_allocations": [
            {"name": "Asha", "count": 60},
            {"name": "Neha", "count": 40},
        ],
        "budgets": {"max_total_observations": 800},
    }
    task = talent_search_queue.build_task(
        command,
        now=datetime(2026, 7, 21, 12, tzinfo=timezone.utc),
    )
    assert task["schema_version"] == "talent-search-task-v3"
    assert task["requested_contact_count"] == 100
    assert sum(row["count"] for row in task["hr_allocations"]) == 100
    assert task["budgets"]["max_total_observations"] == 800

    oversized = copy.deepcopy(command)
    oversized["requested_contact_count"] = 201
    try:
        talent_search_queue.build_task(oversized)
    except ValueError:
        pass
    else:
        raise AssertionError("Unbounded contact quota was accepted")

    result = {
        "schema_version": "talent-search-result-v1",
        "task_id": task["task_id"],
        "operational_job_ref": task["operational_job_ref"],
        "core_job_ref": task["core_job_ref"],
        "quota_fulfilled": True,
        "applicable": True,
        "selected_contacts": 100,
        "hr_allocations": task["hr_allocations"],
        "frozen_bundle": {
            "plan_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "plan_sha256": "a" * 64,
            "expires_at": "2099-07-22T12:00:00+00:00",
            "stored_on_worker": True,
        },
        "database_changed": False,
        "daily_batches_created": 0,
        "review_tasks_created": 0,
        "lark_calls": 0,
        "contactout_calls": 0,
        "linkedin_profile_pages_opened": 0,
    }
    talent_search_queue.validate_result(
        result,
        task_id=task["task_id"],
        operational_job_ref=task["operational_job_ref"],
        core_job_ref=task["core_job_ref"],
        hr_allocations=task["hr_allocations"],
    )
    unsafe = copy.deepcopy(result)
    unsafe["lark_calls"] = 1
    try:
        talent_search_queue.validate_result(
            unsafe,
            task_id=task["task_id"],
            operational_job_ref=task["operational_job_ref"],
            core_job_ref=task["core_job_ref"],
            hr_allocations=task["hr_allocations"],
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Unsafe preview result was accepted")

    preview_with_rows = copy.deepcopy(result)
    preview_with_rows["publication_manifest"] = [{"candidate_ref": "candidate-001"}]
    try:
        talent_search_queue.validate_result(
            preview_with_rows,
            task_id=task["task_id"],
            operational_job_ref=task["operational_job_ref"],
            core_job_ref=task["core_job_ref"],
            hr_allocations=task["hr_allocations"],
        )
    except ValueError:
        pass
    else:
        raise AssertionError("A preview result exposed directly publishable rows")

    shortfall_with_plan = copy.deepcopy(result)
    shortfall_with_plan["quota_fulfilled"] = False
    shortfall_with_plan["applicable"] = False
    shortfall_with_plan["selected_contacts"] = 99
    try:
        talent_search_queue.validate_result(
            shortfall_with_plan,
            task_id=task["task_id"],
            operational_job_ref=task["operational_job_ref"],
            core_job_ref=task["core_job_ref"],
            hr_allocations=task["hr_allocations"],
        )
    except ValueError:
        pass
    else:
        raise AssertionError("A shortfall result retained a publishable frozen plan")

    print("Nexus persistent search queue contract: PASSED")
    print("Exact HR allocation and frozen publication cohort: PASSED")
    print("Bounded preview and no-write result boundary: PASSED")


if __name__ == "__main__":
    main()
