import importlib
import os

import lark_bitable


def main():
    keys = (
        "APP_ID", "APP_SECRET",
        "RECRUITMENT_LARK_APP_ID", "RECRUITMENT_LARK_APP_SECRET",
    )
    original = {key: os.environ.get(key) for key in keys}
    try:
        os.environ["APP_ID"] = "cli_task_bot_must_not_be_used"
        os.environ["APP_SECRET"] = "task-secret-must-not-be-used"
        os.environ.pop("RECRUITMENT_LARK_APP_ID", None)
        os.environ.pop("RECRUITMENT_LARK_APP_SECRET", None)
        importlib.reload(lark_bitable)
        assert lark_bitable.APP_ID == ""
        assert lark_bitable.APP_SECRET == ""
        missing = lark_bitable.ping()
        assert missing["ok"] is False
        assert "RECRUITMENT_LARK_APP_ID" in missing["error"]

        os.environ["RECRUITMENT_LARK_APP_ID"] = "cli_recruitment_fixture"
        os.environ["RECRUITMENT_LARK_APP_SECRET"] = "recruitment-secret-fixture"
        importlib.reload(lark_bitable)
        assert lark_bitable.APP_ID == "cli_recruitment_fixture"
        assert lark_bitable.APP_SECRET == "recruitment-secret-fixture"
        assert lark_bitable.APP_ID != os.environ["APP_ID"]
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        importlib.reload(lark_bitable)

    print("Task Bot and Recruitment Bot credentials are isolated: PASSED")


if __name__ == "__main__":
    main()
