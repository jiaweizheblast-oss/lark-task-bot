from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from tg_automation.campaigns.service import CampaignService
from tg_automation.core.errors import DomainError
from tg_automation.core.time import utc_now
from tg_automation.destinations.schemas import DestinationCreate
from tg_automation.destinations.service import DestinationService
from tg_automation.integrations.schemas import NexusCampaignDraftRequest, NexusContentEvent
from tg_automation.integrations.service import NexusContentSyncService
from tg_automation.media.schemas import MediaCreate
from tg_automation.media.service import MediaService
from tg_automation.storage.enums import ButtonType, ContentStatus, ContentType, DestinationType
from tg_automation.storage.models import (
    AuditLog,
    CampaignButton,
    ContentItem,
    IntegrationEvent,
)


def event(**changes) -> NexusContentEvent:
    values = {
        "external_event_id": "web-2026-001",
        "content_type": ContentType.NEW_GAME,
        "title": "Fortune Garuda 500",
        "summary": "A new game with daily rewards.",
        "action_url": "https://app.21.game/?jump=game",
    }
    values.update(changes)
    return NexusContentEvent(**values)


def test_emergency_notice_is_not_imported_in_first_release() -> None:
    with pytest.raises(ValueError, match="cannot be imported"):
        event(content_type=ContentType.EMERGENCY_NOTICE)


def test_nexus_event_creates_review_draft_and_audit(session) -> None:
    integration, duplicate = NexusContentSyncService(session).ingest(event(), "nexus-user")
    content = session.get(ContentItem, integration.content_id)
    audit = session.scalar(select(AuditLog))

    assert duplicate is False
    assert content.status == ContentStatus.WAITING_REVIEW
    assert content.source_type == "NEXUS_WEBSITE"
    assert "HOT LAUNCH" in content.caption
    assert content.created_by == "nexus-user"
    assert audit.action == "NEXUS_CONTENT_IMPORTED"


def test_identical_event_is_idempotent(session) -> None:
    service = NexusContentSyncService(session)
    first, first_duplicate = service.ingest(event(), "nexus-user")
    second, second_duplicate = service.ingest(event(), "nexus-user")

    assert first_duplicate is False
    assert second_duplicate is True
    assert second.id == first.id
    assert session.query(IntegrationEvent).count() == 1
    assert session.query(ContentItem).count() == 1


def test_reused_event_id_with_changed_payload_is_rejected(session) -> None:
    service = NexusContentSyncService(session)
    service.ingest(event(), "nexus-user")

    with pytest.raises(DomainError) as exc_info:
        service.ingest(event(summary="Changed after delivery"), "nexus-user")

    assert exc_info.value.code == "INTEGRATION_EVENT_CONFLICT"


def test_website_sync_escapes_html() -> None:
    safe = event(title="<b>Game</b>", summary="<script>alert(1)</script>")
    from tg_automation.integrations.templates import render_nexus_caption

    caption = render_nexus_caption(safe)
    assert "<script>" not in caption
    assert "&lt;script&gt;" in caption


def test_import_list_can_filter_content_type(session) -> None:
    service = NexusContentSyncService(session)
    service.ingest(event(), "nexus-user")
    service.ingest(
        event(
            external_event_id="web-2026-002",
            content_type=ContentType.NEW_FEATURE,
            title="Feature",
        ),
        "nexus-user",
    )

    items = service.list(content_type=ContentType.NEW_GAME.value)

    assert len(items) == 1
    assert items[0].event_type == ContentType.NEW_GAME.value


def test_website_event_creates_one_simple_idempotent_campaign_draft(session) -> None:
    service = NexusContentSyncService(session)
    service.ingest(event(), "nexus-user")
    destination = DestinationService(session).create(
        DestinationCreate(
            name="Website Campaign Test",
            telegram_chat_id="-1007654",
            destination_type=DestinationType.TEST_CHANNEL,
            source_code="website_campaign_test",
            is_test=True,
        )
    )
    media = MediaService(session).create(
        MediaCreate(
            name="New Game image",
            file_path="assets/new-game/launch.jpg",
        )
    )
    payload = NexusCampaignDraftRequest(
        destination_ids=[destination.id],
        scheduled_at=utc_now() + timedelta(hours=2),
        media_id=media.id,
    )

    campaign, duplicate, button_type = service.create_campaign_draft(
        "web-2026-001", payload, "nexus-user"
    )
    repeated, repeated_duplicate, _ = service.create_campaign_draft(
        "web-2026-001", payload, "nexus-user"
    )
    button = session.scalar(select(CampaignButton).where(CampaignButton.campaign_id == campaign.id))

    assert duplicate is False
    assert repeated_duplicate is True
    assert repeated.id == campaign.id
    assert button_type == ButtonType.PLAY_NOW
    assert button.button_type == ButtonType.PLAY_NOW
    assert button.label == "PLAY NOW"
    assert button.target_url.startswith("https://app.21.game/")

    scheduled = CampaignService(session).approve_and_schedule(campaign.id, "approver-1")
    content = session.get(ContentItem, campaign.content_id)
    assert content.status == ContentStatus.APPROVED
    assert scheduled.status.value == "SCHEDULED"


def test_website_campaign_draft_requires_action_url(session) -> None:
    service = NexusContentSyncService(session)
    service.ingest(event(action_url=None), "nexus-user")
    destination = DestinationService(session).create(
        DestinationCreate(
            name="No URL Test",
            telegram_chat_id="-1007655",
            destination_type=DestinationType.TEST_CHANNEL,
            source_code="website_no_url",
            is_test=True,
        )
    )

    with pytest.raises(DomainError) as exc_info:
        service.create_campaign_draft(
            "web-2026-001",
            NexusCampaignDraftRequest(
                destination_ids=[destination.id],
                scheduled_at=utc_now() + timedelta(hours=2),
            ),
            "nexus-user",
        )

    assert exc_info.value.code == "WEBSITE_ACTION_URL_REQUIRED"
