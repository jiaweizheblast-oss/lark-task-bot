from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tg_automation.campaigns.service import PERMISSION_MAX_AGE
from tg_automation.core.config import Settings
from tg_automation.core.time import as_utc, utc_now
from tg_automation.storage.enums import (
    CampaignStatus,
    ContentStatus,
    DeliveryStatus,
    RecordStatus,
)
from tg_automation.storage.models import (
    Campaign,
    CampaignDestination,
    ContentItem,
    MessageDelivery,
    TelegramDestination,
    TrackingEvent,
)


class DashboardService:
    def __init__(self, db: Session, settings: Settings, include_test: bool = False) -> None:
        self.db = db
        self.settings = settings
        self.include_test = include_test

    def overview(self) -> dict:
        now = utc_now()
        cutoff = now - timedelta(hours=24)
        destination_filter = True if self.include_test else TelegramDestination.is_test.is_(False)
        delivery_counts = dict(
            self.db.execute(
                select(MessageDelivery.status, func.count(MessageDelivery.id))
                .join(
                    TelegramDestination,
                    TelegramDestination.id == MessageDelivery.destination_id,
                )
                .where(destination_filter)
                .group_by(MessageDelivery.status)
            ).all()
        )
        sent_24h = self.db.scalar(
            select(func.count(MessageDelivery.id))
            .join(
                TelegramDestination,
                TelegramDestination.id == MessageDelivery.destination_id,
            )
            .where(
                destination_filter,
                MessageDelivery.status == DeliveryStatus.SENT,
                MessageDelivery.sent_at >= cutoff,
            )
        )
        failed_24h = self.db.scalar(
            select(func.count(MessageDelivery.id))
            .join(
                TelegramDestination,
                TelegramDestination.id == MessageDelivery.destination_id,
            )
            .where(
                destination_filter,
                MessageDelivery.status == DeliveryStatus.FAILED,
                MessageDelivery.updated_at >= cutoff,
            )
        )
        clicks_24h = self.db.scalar(
            select(func.count(TrackingEvent.id))
            .join(
                TelegramDestination,
                TelegramDestination.id == TrackingEvent.destination_id,
            )
            .where(
                destination_filter,
                TrackingEvent.event_type == "LINK_CLICK",
                TrackingEvent.occurred_at >= cutoff,
            )
        )
        waiting_review = self.db.scalar(
            select(func.count(ContentItem.id)).where(
                ContentItem.status == ContentStatus.WAITING_REVIEW
            )
        )
        imported_waiting_review = self.db.scalar(
            select(func.count(ContentItem.id)).where(
                ContentItem.status == ContentStatus.WAITING_REVIEW,
                ContentItem.source_type == "NEXUS_WEBSITE",
            )
        )
        campaign_count_query = select(
            Campaign.status, func.count(func.distinct(Campaign.id))
        ).group_by(Campaign.status)
        if not self.include_test:
            campaign_count_query = (
                campaign_count_query.join(
                    CampaignDestination,
                    CampaignDestination.campaign_id == Campaign.id,
                )
                .join(
                    TelegramDestination,
                    TelegramDestination.id == CampaignDestination.destination_id,
                )
                .where(TelegramDestination.is_test.is_(False))
            )
        campaign_counts = dict(self.db.execute(campaign_count_query).all())
        return {
            "generated_at": now.isoformat(),
            "system": {
                "production_sending_enabled": self.settings.global_sending_enabled,
                "test_sending_enabled": self.settings.telegram_test_sending_enabled,
            },
            "attention": {
                "content_waiting_review": int(waiting_review or 0),
                "website_drafts_waiting_review": int(imported_waiting_review or 0),
                "failed_deliveries_last_24h": int(failed_24h or 0),
            },
            "campaigns": {
                status.value.lower(): int(campaign_counts.get(status, 0))
                for status in CampaignStatus
            },
            "deliveries": {
                **{
                    status.value.lower(): int(delivery_counts.get(status, 0))
                    for status in DeliveryStatus
                },
                "sent_last_24h": int(sent_24h or 0),
            },
            "engagement": {"link_clicks_last_24h": int(clicks_24h or 0)},
            "destination_health": self._destination_health(now),
            "upcoming_campaigns": self._upcoming(now),
            "recent_failures": self._recent_failures(),
        }

    def _destination_health(self, now) -> dict:
        query = select(TelegramDestination).where(
            TelegramDestination.status == RecordStatus.ENABLED
        )
        if not self.include_test:
            query = query.where(TelegramDestination.is_test.is_(False))
        items = list(self.db.scalars(query).all())
        ready = missing = stale = tests = 0
        for item in items:
            if item.is_test:
                tests += 1
                ready += 1 if item.bot_can_post else 0
            elif not item.bot_can_post or item.last_permission_check is None:
                missing += 1
            elif as_utc(item.last_permission_check) < now - PERMISSION_MAX_AGE:
                stale += 1
            else:
                ready += 1
        return {
            "enabled": len(items),
            "ready": ready,
            "missing_permission": missing,
            "stale_permission": stale,
            "test_destinations": tests,
        }

    def _upcoming(self, now) -> list[dict]:
        query = (
            select(Campaign, ContentItem.title)
            .join(ContentItem, ContentItem.id == Campaign.content_id)
            .where(
                Campaign.status == CampaignStatus.SCHEDULED,
                Campaign.scheduled_at >= now,
            )
            .order_by(Campaign.scheduled_at)
            .limit(10)
        )
        if not self.include_test:
            query = (
                query.join(
                    CampaignDestination,
                    CampaignDestination.campaign_id == Campaign.id,
                )
                .join(
                    TelegramDestination,
                    TelegramDestination.id == CampaignDestination.destination_id,
                )
                .where(TelegramDestination.is_test.is_(False))
                .distinct()
            )
        rows = self.db.execute(query).all()
        return [
            {
                "campaign_id": campaign.id,
                "campaign_code": campaign.campaign_code,
                "title": title,
                "scheduled_at": campaign.scheduled_at.isoformat(),
            }
            for campaign, title in rows
        ]

    def _recent_failures(self) -> list[dict]:
        query = (
            select(MessageDelivery, Campaign.campaign_code, TelegramDestination.name)
            .join(Campaign, Campaign.id == MessageDelivery.campaign_id)
            .join(
                TelegramDestination,
                TelegramDestination.id == MessageDelivery.destination_id,
            )
            .where(MessageDelivery.status == DeliveryStatus.FAILED)
            .order_by(MessageDelivery.updated_at.desc())
            .limit(10)
        )
        if not self.include_test:
            query = query.where(TelegramDestination.is_test.is_(False))
        return [
            {
                "delivery_id": delivery.id,
                "campaign_code": campaign_code,
                "destination_name": destination_name,
                "error_code": delivery.error_code,
                "error_message": delivery.error_message,
            }
            for delivery, campaign_code, destination_name in self.db.execute(query).all()
        ]
