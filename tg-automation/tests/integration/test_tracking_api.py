from __future__ import annotations

from sqlalchemy import select

from tests.unit.test_campaign_service import build_approved_campaign
from tg_automation.core.config import get_settings
from tg_automation.storage.models import CampaignDestination, TrackingEvent
from tg_automation.tracking.service import TrackingService


async def test_tracking_redirect_records_click_and_sets_anonymous_cookie(client, session) -> None:
    campaign, _ = build_approved_campaign(session)
    placement = session.scalar(select(CampaignDestination))
    settings = get_settings()
    service = TrackingService(session, settings)
    buttons = service.render_buttons_for_destination(
        campaign, placement.destination_id, campaign.rendered_buttons
    )
    session.commit()
    code = buttons[0]["value"].rsplit("/", 1)[-1]

    response = await client.get(f"/r/{code}", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "https://example.com/play"
    assert "tg_vid=" in response.headers["set-cookie"]
    event = session.scalar(select(TrackingEvent))
    assert event.tracking_code == code
    assert event.anonymous_visitor_id
