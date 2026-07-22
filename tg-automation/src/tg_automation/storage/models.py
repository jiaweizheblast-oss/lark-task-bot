from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from tg_automation.core.time import utc_now
from tg_automation.storage.base import Base, IdMixin, TimestampMixin
from tg_automation.storage.enums import (
    ButtonType,
    CampaignStatus,
    ContentStatus,
    ContentType,
    DeliveryStatus,
    DestinationType,
    IntegrationEventStatus,
    PublishMode,
    RecordStatus,
)


def enum_column(enum_type: type, name: str, default: Any) -> Any:
    return mapped_column(
        Enum(enum_type, name=name, native_enum=False, length=40),
        default=default,
        nullable=False,
    )


class ContentItem(IdMixin, TimestampMixin, Base):
    __tablename__ = "content_items"

    content_type: Mapped[ContentType] = enum_column(
        ContentType, "content_type", ContentType.WEBSITE_ANNOUNCEMENT
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    caption: Mapped[str] = mapped_column(Text, nullable=False)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_type: Mapped[str] = mapped_column(String(40), default="MANUAL", nullable=False)
    source_reference: Mapped[str | None] = mapped_column(String(200))
    language: Mapped[str] = mapped_column(String(16), default="en", nullable=False)
    status: Mapped[ContentStatus] = enum_column(
        ContentStatus, "content_status", ContentStatus.DRAFT
    )
    created_by: Mapped[str | None] = mapped_column(String(100))


class MediaAsset(IdMixin, TimestampMixin, Base):
    __tablename__ = "media_assets"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    telegram_file_id: Mapped[str | None] = mapped_column(String(512))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    usage_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[RecordStatus] = enum_column(RecordStatus, "media_status", RecordStatus.ENABLED)


class TelegramDestination(IdMixin, TimestampMixin, Base):
    __tablename__ = "telegram_destinations"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    telegram_chat_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    destination_type: Mapped[DestinationType] = enum_column(
        DestinationType, "destination_type", DestinationType.GROUP
    )
    source_code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    is_test: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    bot_can_post: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_permission_check: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[RecordStatus] = enum_column(
        RecordStatus, "destination_status", RecordStatus.ENABLED
    )


class Campaign(IdMixin, TimestampMixin, Base):
    __tablename__ = "campaigns"

    campaign_code: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    content_id: Mapped[str] = mapped_column(
        ForeignKey("content_items.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    media_id: Mapped[str | None] = mapped_column(
        ForeignKey("media_assets.id", ondelete="SET NULL"), index=True
    )
    publish_mode: Mapped[PublishMode] = enum_column(
        PublishMode, "publish_mode", PublishMode.SCHEDULED
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    display_timezone: Mapped[str] = mapped_column(
        String(64), default="Asia/Kolkata", nullable=False
    )
    status: Mapped[CampaignStatus] = enum_column(
        CampaignStatus, "campaign_status", CampaignStatus.DRAFT
    )
    created_by: Mapped[str | None] = mapped_column(String(100))
    approved_by: Mapped[str | None] = mapped_column(String(100))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rendered_caption: Mapped[str | None] = mapped_column(Text)
    rendered_buttons: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    rendered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CampaignDestination(IdMixin, TimestampMixin, Base):
    __tablename__ = "campaign_destinations"
    __table_args__ = (
        UniqueConstraint("campaign_id", "destination_id", name="campaign_destination"),
    )

    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    destination_id: Mapped[str] = mapped_column(
        ForeignKey("telegram_destinations.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    placement_code: Mapped[str] = mapped_column(String(64), nullable=False)
    tracking_code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    status: Mapped[RecordStatus] = enum_column(
        RecordStatus, "campaign_destination_status", RecordStatus.ENABLED
    )


class CampaignButton(IdMixin, TimestampMixin, Base):
    __tablename__ = "campaign_buttons"
    __table_args__ = (
        UniqueConstraint("campaign_id", "row_number", "position", name="button_position"),
    )

    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    button_type: Mapped[ButtonType] = enum_column(ButtonType, "button_type", ButtonType.CLAIM_NOW)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    target_url: Mapped[str | None] = mapped_column(String(2000))
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    tracking_code: Mapped[str | None] = mapped_column(String(100))
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class MessageDelivery(IdMixin, TimestampMixin, Base):
    __tablename__ = "message_deliveries"
    __table_args__ = (
        UniqueConstraint("campaign_id", "destination_id", name="delivery_target"),
        Index("ix_delivery_ready", "status", "next_attempt_at"),
    )

    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    destination_id: Mapped[str] = mapped_column(
        ForeignKey("telegram_destinations.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    telegram_chat_id: Mapped[str] = mapped_column(String(100), nullable=False)
    telegram_message_id: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[DeliveryStatus] = enum_column(
        DeliveryStatus, "delivery_status", DeliveryStatus.PENDING
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(100))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TrackingLink(IdMixin, TimestampMixin, Base):
    __tablename__ = "tracking_links"
    __table_args__ = (
        UniqueConstraint(
            "campaign_id",
            "destination_id",
            "campaign_button_id",
            name="tracking_link_placement_button",
        ),
    )

    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True, nullable=False
    )
    destination_id: Mapped[str] = mapped_column(
        ForeignKey("telegram_destinations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    campaign_button_id: Mapped[str] = mapped_column(
        ForeignKey("campaign_buttons.id", ondelete="CASCADE"), index=True, nullable=False
    )
    tracking_code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    target_url: Mapped[str] = mapped_column(String(2000), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[RecordStatus] = enum_column(
        RecordStatus, "tracking_link_status", RecordStatus.ENABLED
    )


class TrackingEvent(IdMixin, Base):
    __tablename__ = "tracking_events"

    campaign_id: Mapped[str | None] = mapped_column(
        ForeignKey("campaigns.id", ondelete="SET NULL"), index=True
    )
    destination_id: Mapped[str | None] = mapped_column(
        ForeignKey("telegram_destinations.id", ondelete="SET NULL"), index=True
    )
    tracking_link_id: Mapped[str | None] = mapped_column(
        ForeignKey("tracking_links.id", ondelete="SET NULL"), index=True
    )
    tracking_code: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    anonymous_visitor_id: Mapped[str | None] = mapped_column(String(100))
    event_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True, nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class AuditLog(IdMixin, Base):
    __tablename__ = "audit_logs"

    actor_id: Mapped[str | None] = mapped_column(String(100), index=True)
    action: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(36), index=True)
    before_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True, nullable=False
    )


class IntegrationEvent(IdMixin, Base):
    __tablename__ = "integration_events"
    __table_args__ = (
        UniqueConstraint("source_system", "external_event_id", name="integration_event_source"),
    )

    source_system: Mapped[str] = mapped_column(String(50), nullable=False)
    external_event_id: Mapped[str] = mapped_column(String(100), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    content_id: Mapped[str | None] = mapped_column(
        ForeignKey("content_items.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[IntegrationEventStatus] = enum_column(
        IntegrationEventStatus,
        "integration_event_status",
        IntegrationEventStatus.RECEIVED,
    )
    error_code: Mapped[str | None] = mapped_column(String(100))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True, nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
