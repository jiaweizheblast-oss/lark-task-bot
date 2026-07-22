"""daily IST schedule profiles and slots

Revision ID: d91f4a8c2b73
Revises: c7e551b5f1e9
Create Date: 2026-07-22 20:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d91f4a8c2b73"
down_revision: str | Sequence[str] | None = "c7e551b5f1e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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
        sa.Column("last_generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_daily_schedule_profiles")),
        sa.UniqueConstraint("name", name=op.f("uq_daily_schedule_profiles_name")),
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
        sa.Column("campaign_id", sa.String(length=36), nullable=True),
        sa.Column("content_id", sa.String(length=36), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["content_id"],
            ["content_items.id"],
            name=op.f("fk_daily_schedule_slots_content_id_content_items"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name=op.f("fk_daily_schedule_slots_campaign_id_campaigns"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["daily_schedule_profiles.id"],
            name=op.f("fk_daily_schedule_slots_profile_id_daily_schedule_profiles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_daily_schedule_slots")),
        sa.UniqueConstraint("profile_id", "scheduled_at", name="daily_profile_scheduled_at"),
    )
    with op.batch_alter_table("daily_schedule_slots", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_daily_schedule_slots_content_id"),
            ["content_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_daily_schedule_slots_campaign_id"),
            ["campaign_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_daily_schedule_slots_profile_id"),
            ["profile_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_daily_schedule_slot_ready", ["status", "scheduled_at"], unique=False
        )
    op.create_table(
        "gift_code_queue_items",
        sa.Column("external_id", sa.String(length=100), nullable=False),
        sa.Column("gift_code", sa.String(length=256), nullable=False),
        sa.Column("reward_text", sa.String(length=200), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column("assigned_slot_id", sa.String(length=36), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["assigned_slot_id"],
            ["daily_schedule_slots.id"],
            name=op.f("fk_gift_code_queue_items_assigned_slot_id_daily_schedule_slots"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_gift_code_queue_items")),
        sa.UniqueConstraint("external_id", name=op.f("uq_gift_code_queue_items_external_id")),
        sa.UniqueConstraint("gift_code", name=op.f("uq_gift_code_queue_items_gift_code")),
    )
    with op.batch_alter_table("gift_code_queue_items", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_gift_code_queue_items_assigned_slot_id"),
            ["assigned_slot_id"],
            unique=True,
        )
        batch_op.create_index(
            batch_op.f("ix_gift_code_queue_items_received_at"),
            ["received_at"],
            unique=False,
        )
    with op.batch_alter_table("campaigns", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("automation_slot_id", sa.String(length=36), nullable=True)
        )
        batch_op.create_index(
            batch_op.f("ix_campaigns_automation_slot_id"),
            ["automation_slot_id"],
            unique=True,
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_campaigns_automation_slot_id_daily_schedule_slots"),
            "daily_schedule_slots",
            ["automation_slot_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("campaigns", schema=None) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("fk_campaigns_automation_slot_id_daily_schedule_slots"),
            type_="foreignkey",
        )
        batch_op.drop_index(batch_op.f("ix_campaigns_automation_slot_id"))
        batch_op.drop_column("automation_slot_id")
    with op.batch_alter_table("gift_code_queue_items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_gift_code_queue_items_received_at"))
        batch_op.drop_index(batch_op.f("ix_gift_code_queue_items_assigned_slot_id"))
    op.drop_table("gift_code_queue_items")
    with op.batch_alter_table("daily_schedule_slots", schema=None) as batch_op:
        batch_op.drop_index("ix_daily_schedule_slot_ready")
        batch_op.drop_index(batch_op.f("ix_daily_schedule_slots_profile_id"))
        batch_op.drop_index(batch_op.f("ix_daily_schedule_slots_campaign_id"))
        batch_op.drop_index(batch_op.f("ix_daily_schedule_slots_content_id"))
    op.drop_table("daily_schedule_slots")
    op.drop_table("daily_schedule_profiles")
