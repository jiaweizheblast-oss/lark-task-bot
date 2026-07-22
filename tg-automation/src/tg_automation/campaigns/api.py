from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from tg_automation.campaigns.schemas import (
    ApproveRequest,
    CampaignCreate,
    CampaignPreview,
    CampaignRead,
    CampaignUpdate,
    PreflightRequest,
    ScheduleRequest,
    TestPreviewRequest,
)
from tg_automation.campaigns.service import CampaignService
from tg_automation.core.api import success
from tg_automation.core.auth import Actor, require_approver, require_operator
from tg_automation.core.config import get_settings
from tg_automation.storage.database import get_db
from tg_automation.telegram.gateway import OfficialTelegramGateway, TelegramGateway

router = APIRouter(prefix="/tg/campaigns", tags=["campaigns"])


def get_campaign_telegram_gateway() -> TelegramGateway:
    return OfficialTelegramGateway(get_settings())


def serialize(item) -> dict:
    return CampaignRead.model_validate(item).model_dump(mode="json")


@router.post("")
def create_campaign(
    payload: CampaignCreate,
    actor: Actor = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    effective = payload.model_copy(update={"created_by": payload.created_by or actor.actor_id})
    return success(serialize(CampaignService(db).create(effective)))


@router.get("")
def list_campaigns(db: Session = Depends(get_db)) -> dict:
    return success([serialize(item) for item in CampaignService(db).list()])


@router.get("/{campaign_id}")
def get_campaign(campaign_id: str, db: Session = Depends(get_db)) -> dict:
    return success(serialize(CampaignService(db).get(campaign_id)))


@router.patch("/{campaign_id}")
def update_campaign(
    campaign_id: str,
    payload: CampaignUpdate,
    actor: Actor = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    return success(serialize(CampaignService(db).update(campaign_id, payload, actor.actor_id)))


@router.post("/{campaign_id}/preview")
def preview_campaign(campaign_id: str, db: Session = Depends(get_db)) -> dict:
    preview = CampaignService(db).preview(campaign_id)
    return success(CampaignPreview.model_validate(preview).model_dump(mode="json"))


@router.post("/{campaign_id}/preflight")
def preflight_campaign(
    campaign_id: str,
    payload: PreflightRequest,
    db: Session = Depends(get_db),
) -> dict:
    return success(CampaignService(db).preflight(campaign_id, payload.scheduled_at))


@router.post("/{campaign_id}/send-test-preview")
async def send_test_preview(
    campaign_id: str,
    payload: TestPreviewRequest,
    actor: Actor = Depends(require_operator),
    db: Session = Depends(get_db),
    gateway: TelegramGateway = Depends(get_campaign_telegram_gateway),
) -> dict:
    result = await CampaignService(db).send_test_preview(
        campaign_id,
        payload.destination_id,
        gateway,
        test_sending_enabled=get_settings().telegram_test_sending_enabled,
        actor_id=actor.actor_id,
    )
    return success(result.model_dump())


@router.post("/{campaign_id}/approve")
def approve_campaign(
    campaign_id: str,
    payload: ApproveRequest,
    actor: Actor = Depends(require_approver),
    db: Session = Depends(get_db),
) -> dict:
    return success(serialize(CampaignService(db).approve(campaign_id, actor.actor_id)))


@router.post("/{campaign_id}/approve-and-schedule")
def approve_and_schedule_campaign(
    campaign_id: str,
    actor: Actor = Depends(require_approver),
    db: Session = Depends(get_db),
) -> dict:
    return success(serialize(CampaignService(db).approve_and_schedule(campaign_id, actor.actor_id)))


@router.post("/{campaign_id}/schedule")
def schedule_campaign(
    campaign_id: str,
    payload: ScheduleRequest,
    actor: Actor = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    return success(
        serialize(CampaignService(db).schedule(campaign_id, payload.scheduled_at, actor.actor_id))
    )


@router.post("/{campaign_id}/send-now")
def send_now(
    campaign_id: str,
    actor: Actor = Depends(require_approver),
    db: Session = Depends(get_db),
) -> dict:
    return success(
        serialize(
            CampaignService(db).send_now(
                campaign_id,
                sending_enabled=get_settings().global_sending_enabled,
                actor_id=actor.actor_id,
            )
        )
    )


@router.post("/{campaign_id}/cancel")
def cancel_campaign(
    campaign_id: str,
    actor: Actor = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    return success(serialize(CampaignService(db).cancel(campaign_id, actor.actor_id)))
