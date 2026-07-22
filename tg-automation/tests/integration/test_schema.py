from __future__ import annotations

from sqlalchemy import inspect


def test_core_tables_exist(session) -> None:
    tables = set(inspect(session.bind).get_table_names())

    assert {
        "content_items",
        "media_assets",
        "telegram_destinations",
        "campaigns",
        "campaign_destinations",
        "campaign_buttons",
        "message_deliveries",
        "tracking_events",
        "audit_logs",
    } <= tables
