import db


PUBLICATION_ID = "22222222-2222-4222-8222-222222222222"
TASK_ID = "11111111-1111-4111-8111-111111111111"


class FakeCursor:
    def __init__(self, existing):
        self.existing = dict(existing)
        self.rows = []
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        self.executed.append((normalized, params))
        if normalized.startswith("SELECT * FROM talent_daily_publication"):
            self.rows = [dict(self.existing)]
        elif normalized.startswith("UPDATE talent_daily_publication SET status='queued'"):
            resumed = dict(self.existing)
            resumed.update({
                "status": "queued",
                "attempt_count": 0,
                "receipt": {},
                "last_error_code": None,
            })
            self.rows = [resumed]
        else:
            self.rows = []

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class FakeConnection:
    def __init__(self, existing):
        self.autocommit = True
        self.cursor_value = FakeCursor(existing)
        self.committed = False
        self.rolled_back = False

    def cursor(self, **_kwargs):
        return self.cursor_value

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


def main():
    existing = {
        "publication_id": PUBLICATION_ID,
        "business_date": "2026-07-24",
        "status": "failed",
        "payload_sha256": "a" * 64,
        "attempt_count": 1,
        "receipt": {"error_code": "invalid_publication_task"},
        "last_error_code": "invalid_publication_task",
    }
    connection = FakeConnection(existing)
    db.get_conn = lambda: connection

    resumed = db.queue_talent_daily_publication(
        "33333333-3333-4333-8333-333333333333",
        "2026-07-24",
        {"payload_sha256": "b" * 64},
        [TASK_ID],
    )

    assert resumed["publication_id"] == PUBLICATION_ID
    assert resumed["payload_sha256"] == existing["payload_sha256"]
    assert resumed["status"] == "queued"
    assert resumed["attempt_count"] == 0
    assert connection.committed is True
    assert connection.rolled_back is False

    statements = [
        statement for statement, _params in connection.cursor_value.executed
    ]
    assert any(
        statement.startswith(
            "UPDATE talent_daily_publication SET status='queued'"
        )
        for statement in statements
    )
    assert any(
        "UPDATE talent_search_task t SET publication_status='queued'" in statement
        for statement in statements
    )
    assert not any(
        statement.startswith("INSERT INTO talent_daily_publication ")
        for statement in statements
    )
    assert not any("DELETE FROM" in statement for statement in statements)

    print("Failed immutable publication is resumed in place: PASSED")
    print("Frozen search cohorts and publication identity are preserved: PASSED")


if __name__ == "__main__":
    main()
