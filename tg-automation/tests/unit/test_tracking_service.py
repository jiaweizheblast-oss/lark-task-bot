from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from tests.unit.test_campaign_service import build_approved_campaign
from tg_automation.core.config import Settings
from tg_automation.core.errors import DomainError
from tg_automation.core.time import utc_now
from tg_automation.storage.enums import DestinationType
from tg_automation.storage.models import (
    CampaignDestination,
    TelegramDestination,
    TrackingEvent,
    TrackingLink,
)
from tg_automation.tracking.service import TrackingService


def tracking_settings() -> Settings:
    return Settings(
        app_env="test",
        tracking_allowed_hosts="example.com,t.me",
        tracking_base_url="http://testserver/r",
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.net/event",
        "http://example.com/event",
        "https://user:password@example.com/event",
        "javascript:alert(1)",
    ],
)
def test_tracking_rejects_unapproved_or_unsafe_targets(session, url: str) -> None:
    with pytest.raises(DomainError) as exc_info:
        TrackingService(session, tracking_settings()).validate_target_url(url)

    assert exc_info.value.code == "TRACKING_TARGET_NOT_ALLOWED"


def test_each_destination_gets_distinct_immutable_tracking_links(session) -> None:
    campaign, _ = build_approved_campaign(session)
    first_placement = session.scalar(
        select(CampaignDestination).where(CampaignDestination.campaign_id == campaign.id)
    )
    second_destination = TelegramDestination(
        name="Production Channel",
        telegram_chat_id="-100222",
        destination_type=DestinationType.CHANNEL,
        source_code="official",
        is_test=False,
    )
    session.add(second_destination)
    session.flush()
    session.add(
        CampaignDestination(
            campaign_id=campaign.id,
            destination_id=second_destination.id,
            placement_code="official",
            tracking_code="official_tracking",
        )
    )
    session.commit()
    service = TrackingService(session, tracking_settings())

    first = service.render_buttons_for_destination(
        campaign, first_placement.destination_id, campaign.rendered_buttons
    )
    second = service.render_buttons_for_destination(
        campaign, second_destination.id, campaign.rendered_buttons
    )
    session.commit()

    assert first[0]["value"].startswith("http://testserver/r/tg_")
    assert second[0]["value"].startswith("http://testserver/r/tg_")
    assert first[0]["value"] != second[0]["value"]
    assert session.query(TrackingLink).count() == 2

    # Re-rendering is idempotent and returns the existing immutable link.
    repeated = service.render_buttons_for_destination(
        campaign, first_placement.destination_id, campaign.rendered_buttons
    )
    assert repeated[0]["value"] == first[0]["value"]
    assert session.query(TrackingLink).count() == 2


def test_repeat_click_is_flagged_without_losing_event(session) -> None:
    campaign, _ = build_approved_campaign(session)
    placement = session.scalar(select(CampaignDestination))
    service = TrackingService(session, tracking_settings())
    buttons = service.render_buttons_for_destination(
        campaign, placement.destination_id, campaign.rendered_buttons
    )
    session.commit()
    code = buttons[0]["value"].rsplit("/", 1)[-1]
    link = service.resolve(code)

    first = service.record_click(link, "visitor-1", "browser")
    second = service.record_click(link, "visitor-1", "browser")

    assert first.event_data["is_repeat_within_5m"] is False
    assert second.event_data["is_repeat_within_5m"] is True
    assert session.query(TrackingEvent).count() == 2


def test_expired_tracking_link_does_not_redirect(session) -> None:
    campaign, _ = build_approved_campaign(session)
    placement = session.scalar(select(CampaignDestination))
    service = TrackingService(session, tracking_settings())
    buttons = service.render_buttons_for_destination(
        campaign, placement.destination_id, campaign.rendered_buttons
    )
    session.commit()
    code = buttons[0]["value"].rsplit("/", 1)[-1]
    link = session.scalar(select(TrackingLink).where(TrackingLink.tracking_code == code))
    link.expires_at = utc_now() - timedelta(seconds=1)
    session.commit()

    with pytest.raises(DomainError) as exc_info:
        service.resolve(code)

    assert exc_info.value.status_code == 410
