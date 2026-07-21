import copy
from datetime import datetime, timezone

import talent_search_queue


def main():
    command = {
        "task_id": "97871e24-6f1c-41ce-b066-dc79a60aacd4",
        "core_job_ref": "job-core-001",
        "requested_contact_count": 100,
        "max_review_pool_count": 10,
        "budgets": {"max_total_observations": 800},
    }
    task = talent_search_queue.build_task(
        command,
        now=datetime(2026, 7, 21, 12, tzinfo=timezone.utc),
    )
    assert task["schema_version"] == "talent-search-task-v1"
    assert task["requested_contact_count"] == 100
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
        "core_job_ref": task["core_job_ref"],
        "quota_fulfilled": True,
        "applicable": True,
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
        core_job_ref=task["core_job_ref"],
    )
    unsafe = copy.deepcopy(result)
    unsafe["lark_calls"] = 1
    try:
        talent_search_queue.validate_result(
            unsafe,
            task_id=task["task_id"],
            core_job_ref=task["core_job_ref"],
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Unsafe preview result was accepted")

    print("Nexus persistent search queue contract: PASSED")
    print("Bounded preview and no-write result boundary: PASSED")


if __name__ == "__main__":
    main()
