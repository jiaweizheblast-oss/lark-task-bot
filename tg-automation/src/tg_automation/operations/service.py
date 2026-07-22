from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tg_automation.core.audit import audit
from tg_automation.core.errors import DomainError, NotFoundError
from tg_automation.core.time import as_utc, utc_now
from tg_automation.storage.enums import CampaignStatus, DeliveryStatus, RecordStatus
from tg_automation.storage.models import (
    Campaign,
    ContentItem,
    MessageDelivery,
    TelegramDestination,
)


class OperationsService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def queue_overview(self, include_test: bool = False) -> dict:
        query = (
            select(MessageDelivery.status, func.count(MessageDelivery.id))
            .join(
                TelegramDestination,
                TelegramDestination.id == MessageDelivery.destination_id,
            )
            .group_by(MessageDelivery.status)
        )
        stale_query = (
            select(func.count(MessageDelivery.id))
            .join(
                TelegramDestination,
                TelegramDestination.id == MessageDelivery.destination_id,
            )
            .where(
                MessageDelivery.status == DeliveryStatus.SENDING,
                MessageDelivery.lease_expires_at < utc_now(),
            )
        )
        if not include_test:
            query = query.where(TelegramDestination.is_test.is_(False))
            stale_query = stale_query.where(TelegramDestination.is_test.is_(False))
        counts = dict(self.db.execute(query).all())
        return {
            "counts": {
                status.value.lower(): int(counts.get(status, 0)) for status in DeliveryStatus
            },
            "stale_leases": int(self.db.scalar(stale_query) or 0),
        }

    def campaign_deliveries(self, campaign_id: str) -> list[dict]:
        if self.db.get(Campaign, campaign_id) is None:
            raise NotFoundError("campaign", campaign_id)
        rows = self.db.execute(
            select(MessageDelivery, TelegramDestination)
            .join(
                TelegramDestination,
                TelegramDestination.id == MessageDelivery.destination_id,
            )
            .where(MessageDelivery.campaign_id == campaign_id)
            .order_by(TelegramDestination.name)
        ).all()
        return [
            {
                "delivery_id": delivery.id,
                "destination_id": destination.id,
                "destination_name": destination.name,
                "is_test": destination.is_test,
                "status": delivery.status.value,
                "attempt_count": delivery.attempt_count,
                "telegram_message_id": delivery.telegram_message_id,
                "sent_at": delivery.sent_at.isoformat() if delivery.sent_at else None,
                "next_attempt_at": (
                    delivery.next_attempt_at.isoformat() if delivery.next_attempt_at else None
                ),
                "error_code": delivery.error_code,
                "error_message": delivery.error_message,
            }
            for delivery, destination in rows
        ]

    def retry_public_delivery(self, delivery_id: str, actor_id: str) -> MessageDelivery:
        delivery = self.db.get(MessageDelivery, delivery_id)
        if delivery is None:
            raise NotFoundError("delivery", delivery_id)
        if delivery.status != DeliveryStatus.FAILED:
            raise DomainError(
                "DELIVERY_NOT_RETRYABLE",
                "Only failed deliveries can be manually retried.",
                409,
            )
        campaign = self.db.get(Campaign, delivery.campaign_id)
        destination = self.db.get(TelegramDestination, delivery.destination_id)
        if campaign is None or destination is None:
            raise DomainError("DELIVERY_REFERENCE_MISSING", "Delivery references are missing.", 409)
        if destination.status != RecordStatus.ENABLED:
            raise DomainError("DESTINATION_DISABLED", "Destination is disabled.", 422)
        content = self.db.get(ContentItem, campaign.content_id)
        if content and content.valid_until and as_utc(content.valid_until) <= utc_now():
            raise DomainError("CONTENT_EXPIRED", "Expired content cannot be retried.", 422)
        previous = {"status": delivery.status.value, "error_code": delivery.error_code}
        delivery.status = DeliveryStatus.PENDING
        delivery.attempt_count = 0
        delivery.next_attempt_at = utc_now()
        delivery.error_code = None
        delivery.error_message = None
        delivery.locked_at = None
        delivery.locked_by = None
        delivery.lease_expires_at = None
        campaign.status = CampaignStatus.SCHEDULED
        campaign.scheduled_at = delivery.next_attempt_at
        audit(
            self.db,
            actor_id=actor_id,
            action="DELIVERY_MANUAL_RETRY",
            resource_type="delivery",
            resource_id=delivery.id,
            before=previous,
            after={"status": delivery.status.value},
        )
        self.db.commit()
        self.db.refresh(delivery)
        return delivery
