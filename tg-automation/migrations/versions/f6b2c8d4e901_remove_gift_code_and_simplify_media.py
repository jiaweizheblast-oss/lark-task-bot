"""remove Gift Code automation and simplify media

Revision ID: f6b2c8d4e901
Revises: e4a7b1c9d205
Create Date: 2026-07-22 20:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6b2c8d4e901"
down_revision: str | Sequence[str] | None = "e4a7b1c9d205"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("campaigns") as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_campaigns_automation_slot_id_daily_schedule_slots"),
            type_="foreignkey",
        )
        batch_op.drop_index(batch_op.f("ix_campaigns_automation_slot_id"))
        batch_op.drop_column("automation_slot_id")

    op.drop_table("gift_code_queue_items")
    op.drop_table("daily_schedule_slots")
    op.drop_table("daily_schedule_profiles")

    with op.batch_alter_table("content_items") as batch_op:
        batch_op.drop_column("reward_text")
        batch_op.drop_column("gift_code")

    with op.batch_alter_table("media_assets") as batch_op:
        batch_op.drop_column("is_default")
        batch_op.drop_column("active_until")
        batch_op.drop_column("active_from")
        batch_op.drop_column("language")
        batch_op.drop_column("category")


def downgrade() -> None:
    with op.batch_alter_table("media_assets") as batch_op:
        batch_op.add_column(
            sa.Column(
                "category",
                sa.Enum(
                    "GIFT_CODE",
                    "WEBSITE_ANNOUNCEMENT",
                    "NEW_GAME",
                    "NEW_FEATURE",
                    "DAILY_EVENT",
                    "LUCKY_SPIN",
                    "BANK_DELAY",
                    "DEPOSIT_BONUS",
                    "WELCOME_BONUS",
                    "VIP_BONUS",
                    "EMERGENCY_NOTICE",
                    "INDUSTRY_CONTENT",
                    name="media_category",
                    native_enum=False,
                    length=40,
                ),
                nullable=False,
                server_default="WEBSITE_ANNOUNCEMENT",
            )
        )
        batch_op.add_column(
            sa.Column("language", sa.String(length=16), nullable=False, server_default="en")
        )
        batch_op.add_column(sa.Column("active_from", sa.DateTime(timezone=True)))
        batch_op.add_column(sa.Column("active_until", sa.DateTime(timezone=True)))
        batch_op.add_column(
            sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false())
        )

    with op.batch_alter_table("content_items") as batch_op:
        batch_op.add_column(sa.Column("gift_code", sa.String(length=256)))
        batch_op.add_column(sa.Column("reward_text", sa.String(length=200)))

    op.create_table(
        "daily_schedule_profiles",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("local_times", sa.JSON(), nullable=False),
        sa.Column("destination_ids", sa.JSON(), nullable=False),
        sa.Column("claim_url", sa.String(length=2000), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "ENABLED",
                "DISABLED",
                name="schedule_profile_status",
                native_enum=False,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column("last_generated_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "daily_schedule_slots",
        sa.Column("profile_id", sa.String(length=36), nullable=False),
        sa.Column("local_date", sa.String(length=10), nullable=False),
        sa.Column("local_time", sa.String(length=5), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "WAITING_CODE",
                "READY",
                "SCHEDULED",
                "MISSED",
                "CANCELLED",
                name="schedule_slot_status",
                native_enum=False,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column("campaign_id", sa.String(length=36)),
        sa.Column("content_id", sa.String(length=36)),
        sa.Column("error_code", sa.String(length=100)),
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["daily_schedule_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["content_id"], ["content_items.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("profile_id", "scheduled_at", name="daily_profile_scheduled_at"),
    )
    with op.batch_alter_table("daily_schedule_slots") as batch_op:
        batch_op.create_index("ix_daily_schedule_slots_profile_id", ["profile_id"])
        batch_op.create_index("ix_daily_schedule_slots_campaign_id", ["campaign_id"])
        batch_op.create_index("ix_daily_schedule_slots_content_id", ["content_id"])
        batch_op.create_index("ix_daily_schedule_slot_ready", ["status", "scheduled_at"])

    op.create_table(
        "gift_code_queue_items",
        sa.Column("external_id", sa.String(length=100), nullable=False, unique=True),
        sa.Column("gift_code", sa.String(length=256), nullable=False, unique=True),
        sa.Column("reward_text", sa.String(length=200), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True)),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "AVAILABLE",
                "ASSIGNED",
                "EXPIRED",
                "CANCELLED",
                name="gift_code_queue_status",
                native_enum=False,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column("assigned_slot_id", sa.String(length=36)),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["assigned_slot_id"], ["daily_schedule_slots.id"], ondelete="SET NULL"
        ),
    )
    with op.batch_alter_table("gift_code_queue_items") as batch_op:
        batch_op.create_index(
            "ix_gift_code_queue_items_assigned_slot_id", ["assigned_slot_id"], unique=True
        )
        batch_op.create_index("ix_gift_code_queue_items_received_at", ["received_at"])

    with op.batch_alter_table("campaigns") as batch_op:
        batch_op.add_column(sa.Column("automation_slot_id", sa.String(length=36)))
        batch_op.create_index(
            "ix_campaigns_automation_slot_id", ["automation_slot_id"], unique=True
        )
        batch_op.create_foreign_key(
            "fk_campaigns_automation_slot_id_daily_schedule_slots",
            "daily_schedule_slots",
            ["automation_slot_id"],
            ["id"],
            ondelete="SET NULL",
        )
