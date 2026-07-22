from __future__ import annotations

from tests.unit.test_campaign_service import build_approved_campaign
from tg_automation.campaigns.service import CampaignService
from tg_automation.core.config import Settings
from tg_automation.dashboard.service import DashboardService
from tg_automation.integrations.schemas import NexusContentEvent
from tg_automation.integrations.service import NexusContentSyncService
from tg_automation.storage.enums import ContentType


def test_dashboard_excludes_test_delivery_by_default(session) -> None:
    campaign, _service = build_approved_campaign(session)
    CampaignService(session).send_now(campaign.id, sending_enabled=True)
    settings = Settings(app_env="test")

    production = DashboardService(session, settings).overview()
    technical = DashboardService(session, settings, include_test=True).overview()

    assert production["deliveries"]["pending"] == 0
    assert technical["deliveries"]["pending"] == 1
    assert technical["destination_health"]["test_destinations"] == 1


def test_dashboard_reports_safety_switches_without_secrets(session) -> None:
    data = DashboardService(
        session,
        Settings(
            app_env="test",
            global_sending_enabled=True,
            telegram_bot_token="dummy-token",
            telegram_test_sending_enabled=False,
        ),
    ).overview()

    assert data["system"] == {
        "production_sending_enabled": True,
        "test_sending_enabled": False,
    }
    assert "telegram_bot_token" not in str(data)


def test_dashboard_surfaces_website_drafts_waiting_for_review(session) -> None:
    NexusContentSyncService(session).ingest(
        NexusContentEvent(
            external_event_id="dashboard-web-1",
            content_type=ContentType.WEBSITE_ANNOUNCEMENT,
            title="Service update",
            summary="A new website notice is ready for Telegram review.",
        ),
        "nexus-site",
    )

    data = DashboardService(session, Settings(app_env="test")).overview()

    assert data["attention"]["content_waiting_review"] == 1
    assert data["attention"]["website_drafts_waiting_review"] == 1
