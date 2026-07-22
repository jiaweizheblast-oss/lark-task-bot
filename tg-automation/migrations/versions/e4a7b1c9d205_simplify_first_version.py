"""simplify first version

Revision ID: e4a7b1c9d205
Revises: d91f4a8c2b73
Create Date: 2026-07-22 19:20:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4a7b1c9d205"
down_revision: str | Sequence[str] | None = "d91f4a8c2b73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("private_message_deliveries")
    op.drop_table("bot_events")
    op.drop_table("bot_users")

    with op.batch_alter_table("telegram_destinations") as batch_op:
        batch_op.drop_column("bot_can_delete")
        batch_op.drop_column("bot_can_edit")

    with op.batch_alter_table("campaign_destinations") as batch_op:
        batch_op.drop_column("start_parameter")

    with op.batch_alter_table("campaigns") as batch_op:
        batch_op.drop_column("send_to_private_bot")
        batch_op.drop_column("priority")


def downgrade() -> None:
    with op.batch_alter_table("campaigns") as batch_op:
        batch_op.add_column(
            sa.Column(
                "priority",
                sa.Enum(
                    "NORMAL",
                    "MAJOR",
                    "EMERGENCY",
                    name="campaign_priority",
                    native_enum=False,
                    length=40,
                ),
                nullable=False,
                server_default="NORMAL",
            )
        )
        batch_op.add_column(
            sa.Column(
                "send_to_private_bot",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    with op.batch_alter_table("campaign_destinations") as batch_op:
        batch_op.add_column(
            sa.Column("start_parameter", sa.String(length=64), nullable=False, server_default="")
        )

    with op.batch_alter_table("telegram_destinations") as batch_op:
        batch_op.add_column(
            sa.Column("bot_can_edit", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(
            sa.Column("bot_can_delete", sa.Boolean(), nullable=False, server_default=sa.false())
        )

    op.create_table(
        "bot_users",
        sa.Column("telegram_user_id", sa.String(length=100), nullable=False),
        sa.Column("private_chat_id", sa.String(length=100), nullable=False),
        sa.Column("username", sa.String(length=100)),
        sa.Column("language_code", sa.String(length=16)),
        sa.Column("first_source", sa.String(length=100)),
        sa.Column("latest_source", sa.String(length=100)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_interaction_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "notification_preference",
            sa.Enum(
                "ALL_UPDATES",
                "MAJOR_ONLY",
                "PAUSED",
                name="notification_preference",
                native_enum=False,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "BLOCKED",
                "STOPPED",
                name="bot_user_status",
                native_enum=False,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_test", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("private_chat_id"),
        sa.UniqueConstraint("telegram_user_id"),
    )

    op.create_table(
        "bot_events",
        sa.Column("bot_user_id", sa.String(length=36)),
        sa.Column("campaign_id", sa.String(length=36)),
        sa.Column("destination_id", sa.String(length=36)),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("source", sa.String(length=100)),
        sa.Column("event_data", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.ForeignKeyConstraint(["bot_user_id"], ["bot_users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["destination_id"], ["telegram_destinations.id"], ondelete="SET NULL"
        ),
    )
    with op.batch_alter_table("bot_events") as batch_op:
        batch_op.create_index("ix_bot_events_bot_user_id", ["bot_user_id"])
        batch_op.create_index("ix_bot_events_campaign_id", ["campaign_id"])
        batch_op.create_index("ix_bot_events_created_at", ["created_at"])
        batch_op.create_index("ix_bot_events_destination_id", ["destination_id"])
        batch_op.create_index("ix_bot_events_event_type", ["event_type"])

    op.create_table(
        "private_message_deliveries",
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("bot_user_id", sa.String(length=36), nullable=False),
        sa.Column("private_chat_id", sa.String(length=100), nullable=False),
        sa.Column("telegram_message_id", sa.Integer()),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "SENDING",
                "SENT",
                "RETRYING",
                "FAILED",
                "CANCELLED",
                name="private_delivery_status",
                native_enum=False,
                length=40,
            ),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(length=100)),
        sa.Column("error_message", sa.Text()),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("locked_by", sa.String(length=100)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["bot_user_id"], ["bot_users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("campaign_id", "bot_user_id", name="private_delivery_user"),
    )
    with op.batch_alter_table("private_message_deliveries") as batch_op:
        batch_op.create_index("ix_private_delivery_ready", ["status", "next_attempt_at"])
        batch_op.create_index("ix_private_message_deliveries_bot_user_id", ["bot_user_id"])
        batch_op.create_index("ix_private_message_deliveries_campaign_id", ["campaign_id"])
