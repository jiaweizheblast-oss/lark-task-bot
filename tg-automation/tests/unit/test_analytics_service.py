from __future__ import annotations

from sqlalchemy import select

from tests.unit.test_campaign_service import build_approved_campaign
from tg_automation.analytics.service import AnalyticsService
from tg_automation.storage.enums import DeliveryStatus, DestinationType
from tg_automation.storage.models import (
    CampaignDestination,
    MessageDelivery,
    TelegramDestination,
    TrackingEvent,
)


def seed_real_and_test_metrics(session) -> None:
    campaign, _ = build_approved_campaign(session)
    test_placement = session.scalar(select(CampaignDestination))
    test_destination = session.get(TelegramDestination, test_placement.destination_id)
    real_destination = TelegramDestination(
        name="Official Channel",
        telegram_chat_id="-100333",
        destination_type=DestinationType.CHANNEL,
        source_code="official_channel",
        is_test=False,
    )
    session.add(real_destination)
    session.flush()
    session.add_all(
        [
            MessageDelivery(
                campaign_id=campaign.id,
                destination_id=test_destination.id,
                telegram_chat_id=test_destination.telegram_chat_id,
                status=DeliveryStatus.SENT,
            ),
            MessageDelivery(
                campaign_id=campaign.id,
                destination_id=real_destination.id,
                telegram_chat_id=real_destination.telegram_chat_id,
                status=DeliveryStatus.SENT,
            ),
            TrackingEvent(
                campaign_id=campaign.id,
                destination_id=test_destination.id,
                tracking_code="test-click",
                event_type="LINK_CLICK",
                anonymous_visitor_id="test-visitor",
            ),
            TrackingEvent(
                campaign_id=campaign.id,
                destination_id=real_destination.id,
                tracking_code="real-click",
                event_type="LINK_CLICK",
                anonymous_visitor_id="real-visitor",
            ),
        ]
    )
    session.commit()


def test_analytics_excludes_test_traffic_by_default(session) -> None:
    seed_real_and_test_metrics(session)

    metrics = AnalyticsService(session).overview()

    assert metrics == {
        "sent_deliveries": 1,
        "failed_deliveries": 0,
        "link_clicks": 1,
        "unique_visitors": 1,
    }


def test_analytics_can_include_test_traffic_explicitly(session) -> None:
    seed_real_and_test_metrics(session)

    metrics = AnalyticsService(session, include_test=True).overview()

    assert metrics["sent_deliveries"] == 2
    assert metrics["link_clicks"] == 2
    assert metrics["unique_visitors"] == 2
