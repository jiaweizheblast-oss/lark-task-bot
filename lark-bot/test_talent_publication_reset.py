import db


PUBLICATION_ID = "22222222-2222-4222-8222-222222222222"
TASK_ID = "11111111-1111-4111-8111-111111111111"


class FakeCursor:
    def __init__(self, status="publishing"):
        self.status = status
        self.rows = []
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        self.executed.append((normalized, params))
        if normalized.startswith("SELECT publication_id,status"):
            self.rows = [{"publication_id": PUBLICATION_ID, "status": self.status}]
        elif normalized.startswith("SELECT task_id"):
            self.rows = [{"task_id": TASK_ID}]
        else:
            self.rows = []

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class FakeConnection:
    def __init__(self, status="publishing"):
        self.autocommit = True
        self.cursor_value = FakeCursor(status)
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
    active = FakeConnection()
    db.get_conn = lambda: active
    result = db.reset_talent_daily_publication(PUBLICATION_ID)
    assert result == {"status": "reset", "reset": True, "task_count": 1}
    assert active.committed is True and active.rolled_back is False
    sql = [statement for statement, _params in active.cursor_value.executed]
    assert any("status='cancelled'" in statement for statement in sql)
    assert any("publication_status='not_ready'" in statement for statement in sql)
    assert any(statement.startswith("DELETE FROM talent_daily_publication_item") for statement in sql)
    assert any(statement.startswith("DELETE FROM talent_daily_publication WHERE") for statement in sql)

    published = FakeConnection(status="published")
    db.get_conn = lambda: published
    try:
        db.reset_talent_daily_publication(PUBLICATION_ID)
    except ValueError as error:
        assert "published" in str(error)
    else:
        raise AssertionError("a published recruiting table was reset")
    assert published.rolled_back is True

    print("Unpublished publication reset and cohort release: PASSED")
    print("Published recruiting table reset: REJECTED")


if __name__ == "__main__":
    main()
