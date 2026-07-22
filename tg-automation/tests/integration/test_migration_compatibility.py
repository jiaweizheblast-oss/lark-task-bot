from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from tg_automation.core.config import clear_settings_cache


def test_incremental_migration_preserves_existing_rows(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "migration.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    clear_settings_cache()
    config = Config("alembic.ini")

    try:
        command.upgrade(config, "2ce258d3ad54")
        engine = create_engine(database_url)
        now = "2026-07-22 00:00:00"
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO bot_users (
                        telegram_user_id, private_chat_id, started_at,
                        last_interaction_at, notification_preference, status,
                        id, created_at, updated_at
                    ) VALUES (
                        '1', '1', :now, :now, 'MAJOR_ONLY', 'ACTIVE',
                        'user-1', :now, :now
                    )
                    """
                ),
                {"now": now},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO content_items (
                        content_type, title, caption, source_type, language,
                        status, id, created_at, updated_at
                    ) VALUES (
                        'NEW_GAME', 'Game', 'Caption', 'MANUAL', 'en',
                        'APPROVED', 'content-1', :now, :now
                    )
                    """
                ),
                {"now": now},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO campaigns (
                        campaign_code, content_id, publish_mode,
                        display_timezone, status, id, created_at, updated_at
                    ) VALUES (
                        'CMP-1', 'content-1', 'SCHEDULED', 'Asia/Kolkata',
                        'APPROVED', 'campaign-1', :now, :now
                    )
                    """
                ),
                {"now": now},
            )
        engine.dispose()

        command.upgrade(config, "head")
        migrated = create_engine(database_url)
        with migrated.connect() as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                )
            }
            assert "bot_users" not in tables
            assert "bot_events" not in tables
            assert "private_message_deliveries" not in tables
            assert "gift_code_queue_items" not in tables
            assert "daily_schedule_slots" not in tables
            assert "daily_schedule_profiles" not in tables
            campaign_columns = {
                row[1] for row in connection.execute(text("PRAGMA table_info(campaigns)"))
            }
            assert "priority" not in campaign_columns
            assert "send_to_private_bot" not in campaign_columns
            assert "automation_slot_id" not in campaign_columns
            media_columns = {
                row[1] for row in connection.execute(text("PRAGMA table_info(media_assets)"))
            }
            assert "category" not in media_columns
            assert "language" not in media_columns
        migrated.dispose()
    finally:
        clear_settings_cache()
