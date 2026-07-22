from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from tg_automation.campaigns.schemas import (
    CampaignButtonCreate,
    CampaignCreate,
    CampaignUpdate,
)
from tg_automation.campaigns.service import CampaignService
from tg_automation.content.schemas import ContentCreate
from tg_automation.content.service import ContentService
from tg_automation.core.errors import DomainError
from tg_automation.core.time import utc_now
from tg_automation.media.schemas import MediaCreate
from tg_automation.media.service import MediaService
from tg_automation.storage.enums import (
    ButtonType,
    CampaignStatus,
    ContentType,
    DeliveryStatus,
    DestinationType,
)
from tg_automation.storage.models import (
    AuditLog,
    CampaignButton,
    MessageDelivery,
    TelegramDestination,
)
from tg_automation.telegram.schemas import TelegramSendResult


@dataclass
class PreviewGateway:
    calls: int = 0

    async def send_photo(self, chat_id, photo, caption, buttons):
        self.calls += 1
        return TelegramSendResult(chat_id=chat_id, message_id=8080)

    async def check_permissions(self, chat_id):  # pragma: no cover - protocol completeness
        raise NotImplementedError


def build_approved_campaign(
    session,
):
    content_service = ContentService(session)
    content = content_service.create(
        ContentCreate(
            content_type=ContentType.NEW_GAME,
            title="New Game",
            caption="{{title}} is now online.",
            valid_until=utc_now() + timedelta(hours=4),
        )
    )
    content_service.approve(content.id)
    media = MediaService(session).create(
        MediaCreate(
            name="New Game image",
            file_path="assets/new-game.jpg",
        )
    )
    destination = TelegramDestination(
        name="Test Channel",
        telegram_chat_id="-100111",
        destination_type=DestinationType.TEST_CHANNEL,
        source_code="test_channel",
        is_test=True,
    )
    session.add(destination)
    session.commit()
    session.refresh(destination)

    campaign_service = CampaignService(session)
    campaign = campaign_service.create(
        CampaignCreate(
            content_id=content.id,
            media_id=media.id,
            destination_ids=[destination.id],
            scheduled_at=utc_now() + timedelta(hours=1),
            buttons=[
                CampaignButtonCreate(
                    button_type=ButtonType.PLAY_NOW,
                    label="PLAY NOW",
                    target_url="https://example.com/play",
                    row_number=0,
                    position=0,
                ),
            ],
        )
    )
    return campaign_service.approve(campaign.id, "approver"), campaign_service


def test_approval_freezes_rendered_snapshot(session) -> None:
    campaign, _service = build_approved_campaign(session)

    assert campaign.status == CampaignStatus.APPROVED
    assert "New Game" in campaign.rendered_caption
    assert campaign.rendered_buttons[0]["value"] == "https://example.com/play"
    assert len(campaign.rendered_buttons) == 1
    assert campaign.rendered_at is not None


def test_campaign_rejects_duplicate_or_blank_button_labels() -> None:
    with pytest.raises(ValidationError, match="labels must be unique"):
        CampaignCreate(
            content_id="content-1",
            destination_ids=["destination-1"],
            scheduled_at=utc_now() + timedelta(hours=1),
            buttons=[
                CampaignButtonCreate(
                    button_type=ButtonType.PLAY_NOW,
                    label="Open Event",
                    target_url="https://example.com/event",
                    row_number=0,
                    position=0,
                ),
                CampaignButtonCreate(
                    button_type=ButtonType.VIEW_DETAILS,
                    label="open event",
                    target_url="https://example.com/details",
                    row_number=0,
                    position=1,
                ),
            ],
        )
    with pytest.raises(ValidationError, match="cannot be blank"):
        CampaignButtonCreate(
            button_type=ButtonType.VIEW_DETAILS,
            label="   ",
            target_url="https://example.com/details",
            row_number=0,
            position=0,
        )


def test_schedule_creates_one_delivery(session) -> None:
    campaign, service = build_approved_campaign(session)

    scheduled = service.schedule(campaign.id, utc_now() + timedelta(hours=2))
    deliveries = session.query(MessageDelivery).filter_by(campaign_id=campaign.id).all()

    assert scheduled.status == CampaignStatus.SCHEDULED
    assert len(deliveries) == 1
    assert deliveries[0].status == DeliveryStatus.PENDING


def test_global_switch_blocks_send_now(session) -> None:
    campaign, service = build_approved_campaign(session)

    with pytest.raises(DomainError, match="Global sending"):
        service.send_now(campaign.id, sending_enabled=False)


def test_schedule_rejects_time_after_content_expiry(session) -> None:
    campaign, service = build_approved_campaign(session)

    with pytest.raises(DomainError) as exc_info:
        service.schedule(campaign.id, utc_now() + timedelta(hours=5))

    assert exc_info.value.code == "CONTENT_EXPIRES_BEFORE_SEND"


def test_schedule_rechecks_real_destination_permission(session) -> None:
    campaign, service = build_approved_campaign(session)
    destination = session.scalar(
        select(TelegramDestination).where(TelegramDestination.is_test.is_(True))
    )
    destination.is_test = False
    destination.bot_can_post = False
    session.commit()

    with pytest.raises(DomainError) as exc_info:
        service.schedule(campaign.id, utc_now() + timedelta(hours=2))

    assert exc_info.value.code == "DESTINATION_PERMISSION_UNVERIFIED"


def test_draft_campaign_can_replace_buttons_before_approval(session) -> None:
    campaign, service = build_approved_campaign(session)
    campaign.status = CampaignStatus.DRAFT
    session.commit()

    updated = service.update(
        campaign.id,
        CampaignUpdate(
            buttons=[
                CampaignButtonCreate(
                    button_type=ButtonType.CLAIM_NOW,
                    label="PLAY NOW",
                    target_url="https://example.com/play",
                    row_number=0,
                    position=0,
                )
            ]
        ),
        "operator-1",
    )
    buttons = session.scalars(
        select(CampaignButton).where(CampaignButton.campaign_id == campaign.id)
    ).all()

    assert updated.rendered_caption is None
    assert len(buttons) == 1
    assert buttons[0].label == "PLAY NOW"


def test_approved_campaign_cannot_be_edited(session) -> None:
    campaign, service = build_approved_campaign(session)

    with pytest.raises(DomainError) as exc_info:
        service.update(campaign.id, CampaignUpdate(display_timezone="UTC"))

    assert exc_info.value.code == "CAMPAIGN_NOT_EDITABLE"


def test_preflight_separates_configuration_from_final_approval(session) -> None:
    campaign, service = build_approved_campaign(session)
    campaign.status = CampaignStatus.DRAFT
    session.commit()

    report = service.preflight(campaign.id, utc_now() + timedelta(hours=2))

    assert report["configuration_ready"] is True
    assert report["dispatch_ready"] is False
    approval = next(item for item in report["checks"] if item["name"] == "campaign_approved")
    assert approval["passed"] is False


def test_schedule_rejects_stale_real_destination_permission(session) -> None:
    campaign, service = build_approved_campaign(session)
    destination = session.scalar(
        select(TelegramDestination).where(TelegramDestination.is_test.is_(True))
    )
    destination.is_test = False
    destination.bot_can_post = True
    destination.last_permission_check = utc_now() - timedelta(hours=25)
    session.commit()

    with pytest.raises(DomainError) as exc_info:
        service.schedule(campaign.id, utc_now() + timedelta(hours=2))

    assert exc_info.value.code == "DESTINATION_PERMISSION_STALE"


async def test_campaign_test_preview_uses_snapshot_without_creating_delivery(session) -> None:
    campaign, service = build_approved_campaign(session)
    campaign.status = CampaignStatus.DRAFT
    campaign.rendered_caption = None
    campaign.rendered_buttons = None
    campaign.rendered_at = None
    session.commit()
    destination = session.scalar(
        select(TelegramDestination).where(TelegramDestination.is_test.is_(True))
    )
    gateway = PreviewGateway()

    result = await service.send_test_preview(
        campaign.id,
        destination.id,
        gateway,
        test_sending_enabled=True,
        actor_id="operator-1",
    )

    session.refresh(campaign)
    assert result.message_id == 8080
    assert gateway.calls == 1
    assert campaign.status == CampaignStatus.DRAFT
    assert (
        session.scalar(select(MessageDelivery).where(MessageDelivery.campaign_id == campaign.id))
        is None
    )
    audit = session.scalar(select(AuditLog).where(AuditLog.action == "CAMPAIGN_TEST_PREVIEW_SENT"))
    assert audit.actor_id == "operator-1"


async def test_campaign_test_preview_obeys_separate_safety_switch(session) -> None:
    campaign, service = build_approved_campaign(session)
    destination = session.scalar(
        select(TelegramDestination).where(TelegramDestination.is_test.is_(True))
    )
    gateway = PreviewGateway()

    with pytest.raises(DomainError) as exc_info:
        await service.send_test_preview(
            campaign.id,
            destination.id,
            gateway,
            test_sending_enabled=False,
        )

    assert exc_info.value.code == "TEST_SENDING_DISABLED"
    assert gateway.calls == 0
