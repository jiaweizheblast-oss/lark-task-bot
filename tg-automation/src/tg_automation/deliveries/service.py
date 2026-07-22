from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from tg_automation.core.config import Settings, get_settings
from tg_automation.core.errors import DomainError, NotFoundError
from tg_automation.core.time import as_utc, utc_now
from tg_automation.storage.enums import (
    CampaignStatus,
    DeliveryStatus,
    RecordStatus,
)
from tg_automation.storage.models import (
    Campaign,
    MediaAsset,
    MessageDelivery,
    TelegramDestination,
)
from tg_automation.telegram.gateway import TelegramGateway
from tg_automation.telegram.schemas import TelegramButton
from tg_automation.tracking.service import TrackingService

RETRYABLE_CODES = {"TELEGRAM_RATE_LIMITED", "TELEGRAM_NETWORK_ERROR"}
RETRY_DELAYS = (30, 120, 600)


class DeliveryService:
    def __init__(self, db: Session, worker_id: str, settings: Settings | None = None) -> None:
        self.db = db
        self.worker_id = worker_id
        self.settings = settings or get_settings()

    def claim_ready(self, limit: int = 20, *, test_only: bool = False) -> list[str]:
        now = utc_now()
        query = (
            select(MessageDelivery)
            .join(Campaign, Campaign.id == MessageDelivery.campaign_id)
            .join(
                TelegramDestination,
                TelegramDestination.id == MessageDelivery.destination_id,
            )
            .where(
                Campaign.status.in_([CampaignStatus.SCHEDULED, CampaignStatus.SENDING]),
                Campaign.scheduled_at <= now,
                MessageDelivery.status.in_(
                    [
                        DeliveryStatus.PENDING,
                        DeliveryStatus.RETRYING,
                        DeliveryStatus.SENDING,
                    ]
                ),
                MessageDelivery.next_attempt_at <= now,
                (
                    MessageDelivery.lease_expires_at.is_(None)
                    | (MessageDelivery.lease_expires_at < now)
                ),
            )
        )
        if test_only:
            query = query.where(TelegramDestination.is_test.is_(True))
        rows = list(
            self.db.scalars(
                query
                .order_by(MessageDelivery.next_attempt_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ).all()
        )
        delivery_ids: list[str] = []
        for item in rows:
            item.status = DeliveryStatus.SENDING
            item.locked_at = now
            item.locked_by = self.worker_id
            item.lease_expires_at = now + timedelta(minutes=5)
            campaign = self.db.get(Campaign, item.campaign_id)
            if campaign and campaign.status == CampaignStatus.SCHEDULED:
                campaign.status = CampaignStatus.SENDING
            delivery_ids.append(item.id)
        self.db.commit()
        return delivery_ids

    async def process(self, delivery_id: str, gateway: TelegramGateway) -> MessageDelivery:
        delivery = self.db.get(MessageDelivery, delivery_id)
        if delivery is None:
            raise NotFoundError("delivery", delivery_id)
        if delivery.status != DeliveryStatus.SENDING or delivery.locked_by != self.worker_id:
            raise DomainError(
                "DELIVERY_NOT_OWNED",
                "Delivery is not locked by this worker.",
                409,
            )

        campaign = self.db.get(Campaign, delivery.campaign_id)
        if campaign is None:
            raise NotFoundError("campaign", delivery.campaign_id)
        destination = self.db.get(TelegramDestination, delivery.destination_id)
        if destination is None or destination.status != RecordStatus.ENABLED:
            self._mark_failure(
                delivery,
                "DESTINATION_UNAVAILABLE",
                "Destination was disabled or removed before delivery.",
                retryable=False,
            )
            self._update_campaign(campaign)
            self.db.commit()
            return delivery
        if not destination.is_test and (
            not destination.bot_can_post
            or destination.last_permission_check is None
            or as_utc(destination.last_permission_check) < utc_now() - timedelta(hours=24)
        ):
            self._mark_failure(
                delivery,
                "DESTINATION_PERMISSION_INVALID",
                "Destination posting permission is missing or stale.",
                retryable=False,
            )
            self._update_campaign(campaign)
            self.db.commit()
            return delivery
        if not campaign.rendered_caption or not campaign.media_id:
            self._mark_failure(
                delivery,
                "DELIVERY_SNAPSHOT_MISSING",
                "Campaign has no approved rendered snapshot.",
                retryable=False,
            )
            self._update_campaign(campaign)
            self.db.commit()
            return delivery
        media = self.db.get(MediaAsset, campaign.media_id)
        if media is None:
            self._mark_failure(
                delivery,
                "MEDIA_NOT_FOUND",
                "Campaign media no longer exists.",
                retryable=False,
            )
            self._update_campaign(campaign)
            self.db.commit()
            return delivery

        try:
            destination_buttons = TrackingService(
                self.db, self.settings
            ).render_buttons_for_destination(
                campaign,
                delivery.destination_id,
                campaign.rendered_buttons or [],
            )
            buttons = [
                TelegramButton(
                    label=item["label"],
                    value=item["value"],
                    row=item["row"],
                    position=item["position"],
                )
                for item in destination_buttons
            ]
        except DomainError as exc:
            self._mark_failure(
                delivery,
                exc.code,
                exc.message,
                retryable=False,
            )
            self._update_campaign(campaign)
            self.db.commit()
            return delivery
        delivery.attempt_count += 1
        delivery.last_attempt_at = utc_now()
        try:
            result = await gateway.send_photo(
                delivery.telegram_chat_id,
                media.telegram_file_id or media.file_path,
                campaign.rendered_caption,
                buttons,
            )
        except DomainError as exc:
            self._mark_failure(
                delivery,
                exc.code,
                exc.message,
                retryable=exc.code in RETRYABLE_CODES,
                retry_after=(exc.details or {}).get("retry_after_seconds"),
            )
        else:
            delivery.telegram_chat_id = result.chat_id
            delivery.telegram_message_id = result.message_id
            delivery.status = DeliveryStatus.SENT
            delivery.sent_at = utc_now()
            delivery.error_code = None
            delivery.error_message = None
            delivery.locked_at = None
            delivery.locked_by = None
            delivery.lease_expires_at = None

        self._update_campaign(campaign)
        self.db.commit()
        self.db.refresh(delivery)
        return delivery

    def _mark_failure(
        self,
        delivery: MessageDelivery,
        code: str,
        message: str,
        *,
        retryable: bool,
        retry_after: int | None = None,
    ) -> None:
        delivery.error_code = code
        delivery.error_message = message[:2000]
        delivery.locked_at = None
        delivery.locked_by = None
        delivery.lease_expires_at = None
        if retryable and delivery.attempt_count <= len(RETRY_DELAYS):
            index = max(delivery.attempt_count - 1, 0)
            delay = retry_after or RETRY_DELAYS[index]
            delivery.status = DeliveryStatus.RETRYING
            delivery.next_attempt_at = utc_now() + timedelta(seconds=delay)
        else:
            delivery.status = DeliveryStatus.FAILED

    def _update_campaign(self, campaign: Campaign) -> None:
        statuses = list(
            self.db.scalars(
                select(MessageDelivery.status).where(MessageDelivery.campaign_id == campaign.id)
            ).all()
        )
        if not statuses:
            return
        if all(status == DeliveryStatus.SENT for status in statuses):
            campaign.status = CampaignStatus.SENT
            campaign.sent_at = utc_now()
        elif all(status in {DeliveryStatus.SENT, DeliveryStatus.FAILED} for status in statuses):
            campaign.status = (
                CampaignStatus.PARTIALLY_SENT
                if DeliveryStatus.SENT in statuses
                else CampaignStatus.FAILED
            )
        elif any(status == DeliveryStatus.SENT for status in statuses):
            campaign.status = CampaignStatus.SENDING
