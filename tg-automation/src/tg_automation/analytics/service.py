from __future__ import annotations

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from tg_automation.storage.enums import DeliveryStatus
from tg_automation.storage.models import (
    Campaign,
    CampaignDestination,
    ContentItem,
    MediaAsset,
    MessageDelivery,
    TelegramDestination,
    TrackingEvent,
)


class AnalyticsService:
    def __init__(self, db: Session, include_test: bool = False) -> None:
        self.db = db
        self.include_test = include_test

    def overview(self) -> dict:
        destination_filter = True if self.include_test else TelegramDestination.is_test.is_(False)
        sent = self.db.scalar(
            select(func.count(MessageDelivery.id))
            .join(
                TelegramDestination,
                TelegramDestination.id == MessageDelivery.destination_id,
            )
            .where(
                MessageDelivery.status == DeliveryStatus.SENT,
                destination_filter,
            )
        )
        failed = self.db.scalar(
            select(func.count(MessageDelivery.id))
            .join(
                TelegramDestination,
                TelegramDestination.id == MessageDelivery.destination_id,
            )
            .where(
                MessageDelivery.status == DeliveryStatus.FAILED,
                destination_filter,
            )
        )
        clicks = self.db.scalar(
            select(func.count(TrackingEvent.id))
            .join(
                TelegramDestination,
                TelegramDestination.id == TrackingEvent.destination_id,
            )
            .where(TrackingEvent.event_type == "LINK_CLICK", destination_filter)
        )
        unique_clicks = self.db.scalar(
            select(func.count(distinct(TrackingEvent.anonymous_visitor_id)))
            .join(
                TelegramDestination,
                TelegramDestination.id == TrackingEvent.destination_id,
            )
            .where(TrackingEvent.event_type == "LINK_CLICK", destination_filter)
        )
        return {
            "sent_deliveries": int(sent or 0),
            "failed_deliveries": int(failed or 0),
            "link_clicks": int(clicks or 0),
            "unique_visitors": int(unique_clicks or 0),
        }

    def campaigns(self) -> list[dict]:
        query = select(Campaign).order_by(Campaign.created_at.desc())
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
        campaigns = self.db.scalars(query).all()
        output: list[dict] = []
        for campaign in campaigns:
            content = self.db.get(ContentItem, campaign.content_id)
            sent, failed = self._delivery_counts(campaign.id)
            clicks, unique_clicks = self._click_counts(campaign.id)
            output.append(
                {
                    "campaign_id": campaign.id,
                    "campaign_code": campaign.campaign_code,
                    "title": content.title if content else None,
                    "content_type": content.content_type.value if content else None,
                    "status": campaign.status.value,
                    "sent_destinations": sent,
                    "failed_destinations": failed,
                    "link_clicks": clicks,
                    "unique_visitors": unique_clicks,
                }
            )
        return output

    def media(self) -> list[dict]:
        items = self.db.scalars(select(MediaAsset).order_by(MediaAsset.name)).all()
        output: list[dict] = []
        for media in items:
            campaign_query = select(Campaign.id).where(Campaign.media_id == media.id)
            if not self.include_test:
                campaign_query = (
                    campaign_query.join(
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
            campaign_ids = list(self.db.scalars(campaign_query).all())
            clicks = sum(self._click_counts(campaign_id)[0] for campaign_id in campaign_ids)
            output.append(
                {
                    "media_id": media.id,
                    "name": media.name,
                    "campaign_count": len(campaign_ids),
                    "link_clicks": clicks,
                    "usage_count": media.usage_count,
                }
            )
        return output

    def destinations(self) -> list[dict]:
        query = select(TelegramDestination).order_by(TelegramDestination.name)
        if not self.include_test:
            query = query.where(TelegramDestination.is_test.is_(False))
        destinations = self.db.scalars(query).all()
        output: list[dict] = []
        for destination in destinations:
            sent = self.db.scalar(
                select(func.count(MessageDelivery.id)).where(
                    MessageDelivery.destination_id == destination.id,
                    MessageDelivery.status == DeliveryStatus.SENT,
                )
            )
            clicks = self.db.scalar(
                select(func.count(TrackingEvent.id)).where(
                    TrackingEvent.destination_id == destination.id,
                    TrackingEvent.event_type == "LINK_CLICK",
                )
            )
            output.append(
                {
                    "destination_id": destination.id,
                    "name": destination.name,
                    "source_code": destination.source_code,
                    "sent_deliveries": int(sent or 0),
                    "link_clicks": int(clicks or 0),
                }
            )
        return output

    def _delivery_counts(self, campaign_id: str) -> tuple[int, int]:
        query = (
            select(MessageDelivery.status, func.count(MessageDelivery.id))
            .join(
                TelegramDestination,
                TelegramDestination.id == MessageDelivery.destination_id,
            )
            .where(MessageDelivery.campaign_id == campaign_id)
            .group_by(MessageDelivery.status)
        )
        if not self.include_test:
            query = query.where(TelegramDestination.is_test.is_(False))
        counts = dict(self.db.execute(query).all())
        return (
            int(counts.get(DeliveryStatus.SENT, 0)),
            int(counts.get(DeliveryStatus.FAILED, 0)),
        )

    def _click_counts(self, campaign_id: str) -> tuple[int, int]:
        query = (
            select(
                func.count(TrackingEvent.id),
                func.count(distinct(TrackingEvent.anonymous_visitor_id)),
            )
            .join(
                TelegramDestination,
                TelegramDestination.id == TrackingEvent.destination_id,
            )
            .where(
                TrackingEvent.campaign_id == campaign_id,
                TrackingEvent.event_type == "LINK_CLICK",
            )
        )
        if not self.include_test:
            query = query.where(TelegramDestination.is_test.is_(False))
        row = self.db.execute(query).one()
        return int(row[0] or 0), int(row[1] or 0)
