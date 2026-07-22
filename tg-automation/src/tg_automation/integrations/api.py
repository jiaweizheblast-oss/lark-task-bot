from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from tg_automation.core.api import success
from tg_automation.core.auth import Actor, require_operator
from tg_automation.integrations.schemas import (
    NexusCampaignDraftRequest,
    NexusCampaignDraftResult,
    NexusContentEvent,
    NexusContentEventRead,
    NexusContentEventResult,
)
from tg_automation.integrations.service import NexusContentSyncService
from tg_automation.storage.database import get_db
from tg_automation.storage.enums import ContentType
from tg_automation.storage.models import ContentItem

router = APIRouter(prefix="/integrations/nexus", tags=["nexus-integration"])


def serialize_event(event, db: Session, duplicate: bool = False) -> dict:
    content = db.get(ContentItem, event.content_id) if event.content_id else None
    return NexusContentEventRead(
        integration_event_id=event.id,
        content_id=event.content_id,
        duplicate=duplicate,
        status=event.status.value,
        external_event_id=event.external_event_id,
        event_type=event.event_type,
        payload=event.payload,
        received_at=event.received_at,
        processed_at=event.processed_at,
        content_status=content.status if content else None,
        content_title=content.title if content else None,
        telegram_caption=content.caption if content else None,
    ).model_dump(mode="json")


@router.post("/content-events")
def ingest_content_event(
    payload: NexusContentEvent,
    actor: Actor = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    event, duplicate = NexusContentSyncService(db).ingest(payload, actor.actor_id)
    result = NexusContentEventResult(
        integration_event_id=event.id,
        content_id=event.content_id,
        duplicate=duplicate,
        status=event.status.value,
    )
    return success(result.model_dump(mode="json"))


@router.get("/content-events")
def list_content_events(
    limit: int = Query(default=50, ge=1, le=100),
    content_type: ContentType | None = None,
    db: Session = Depends(get_db),
) -> dict:
    items = NexusContentSyncService(db).list(limit, content_type.value if content_type else None)
    return success([serialize_event(item, db) for item in items])


@router.get("/content-events/{external_event_id}")
def get_content_event(
    external_event_id: str,
    db: Session = Depends(get_db),
) -> dict:
    return success(serialize_event(NexusContentSyncService(db).get(external_event_id), db))


@router.post("/content-events/{external_event_id}/campaign-draft")
def create_campaign_draft(
    external_event_id: str,
    payload: NexusCampaignDraftRequest,
    actor: Actor = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    campaign, duplicate, button_type = NexusContentSyncService(db).create_campaign_draft(
        external_event_id, payload, actor.actor_id
    )
    result = NexusCampaignDraftResult(
        campaign_id=campaign.id,
        campaign_code=campaign.campaign_code,
        status=campaign.status.value,
        duplicate=duplicate,
        content_id=campaign.content_id,
        button_type=button_type.value,
    )
    return success(result.model_dump(mode="json"))
