from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tg_automation.campaigns.schemas import CampaignButtonCreate, CampaignCreate
from tg_automation.campaigns.service import CampaignService
from tg_automation.core.audit import audit
from tg_automation.core.config import get_settings
from tg_automation.core.errors import DomainError, NotFoundError
from tg_automation.core.time import as_utc, utc_now
from tg_automation.integrations.schemas import NexusCampaignDraftRequest, NexusContentEvent
from tg_automation.integrations.templates import render_nexus_caption
from tg_automation.storage.enums import (
    ButtonType,
    CampaignStatus,
    ContentStatus,
    ContentType,
    IntegrationEventStatus,
    PublishMode,
)
from tg_automation.storage.models import (
    Campaign,
    CampaignButton,
    CampaignDestination,
    ContentItem,
    IntegrationEvent,
)
from tg_automation.tracking.service import TrackingService

WEBSITE_PRIMARY_BUTTONS = {
    ContentType.WEBSITE_ANNOUNCEMENT: (ButtonType.VIEW_DETAILS, "VIEW DETAILS"),
    ContentType.NEW_GAME: (ButtonType.PLAY_NOW, "PLAY NOW"),
    ContentType.NEW_FEATURE: (ButtonType.VIEW_DETAILS, "VIEW DETAILS"),
    ContentType.BANK_DELAY: (ButtonType.VIEW_DETAILS, "VIEW UPDATE"),
    ContentType.DAILY_EVENT: (ButtonType.CLAIM_NOW, "JOIN EVENT"),
    ContentType.LUCKY_SPIN: (ButtonType.SPIN_NOW, "SPIN NOW"),
}


def canonical_payload(event: NexusContentEvent) -> tuple[dict, str]:
    payload = event.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return payload, hashlib.sha256(encoded).hexdigest()


class NexusContentSyncService:
    SOURCE = "NEXUS_WEBSITE"

    def __init__(self, db: Session) -> None:
        self.db = db

    def ingest(self, event: NexusContentEvent, actor_id: str) -> tuple[IntegrationEvent, bool]:
        payload, payload_hash = canonical_payload(event)
        existing = self.db.scalar(
            select(IntegrationEvent).where(
                IntegrationEvent.source_system == self.SOURCE,
                IntegrationEvent.external_event_id == event.external_event_id,
            )
        )
        if existing is not None:
            if existing.payload_hash != payload_hash:
                raise DomainError(
                    "INTEGRATION_EVENT_CONFLICT",
                    "The event ID was already used with different content.",
                    409,
                )
            return existing, True

        content = ContentItem(
            content_type=event.content_type,
            title=event.title.strip(),
            caption=render_nexus_caption(event),
            source_type=self.SOURCE,
            source_reference=event.external_event_id,
            language=event.language,
            status=ContentStatus.WAITING_REVIEW,
            created_by=actor_id,
        )
        self.db.add(content)
        self.db.flush()
        integration = IntegrationEvent(
            source_system=self.SOURCE,
            external_event_id=event.external_event_id,
            event_type=event.content_type.value,
            payload_hash=payload_hash,
            payload=payload,
            content_id=content.id,
            status=IntegrationEventStatus.PROCESSED,
            processed_at=utc_now(),
        )
        self.db.add(integration)
        self.db.flush()
        audit(
            self.db,
            actor_id=actor_id,
            action="NEXUS_CONTENT_IMPORTED",
            resource_type="content",
            resource_id=content.id,
            after={"content_type": event.content_type.value, "event_id": event.external_event_id},
        )
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            concurrent = self.db.scalar(
                select(IntegrationEvent).where(
                    IntegrationEvent.source_system == self.SOURCE,
                    IntegrationEvent.external_event_id == event.external_event_id,
                )
            )
            if concurrent and concurrent.payload_hash == payload_hash:
                return concurrent, True
            raise DomainError(
                "INTEGRATION_EVENT_CONFLICT",
                "The event conflicted with another import.",
                409,
            ) from None
        self.db.refresh(integration)
        return integration, False

    def list(self, limit: int = 50, content_type: str | None = None) -> list[IntegrationEvent]:
        query = select(IntegrationEvent).where(IntegrationEvent.source_system == self.SOURCE)
        if content_type is not None:
            query = query.where(IntegrationEvent.event_type == content_type)
        return list(
            self.db.scalars(query.order_by(IntegrationEvent.received_at.desc()).limit(limit)).all()
        )

    def get(self, external_event_id: str) -> IntegrationEvent:
        item = self.db.scalar(
            select(IntegrationEvent).where(
                IntegrationEvent.source_system == self.SOURCE,
                IntegrationEvent.external_event_id == external_event_id,
            )
        )
        if item is None:
            raise NotFoundError("integration event", external_event_id)
        return item

    def create_campaign_draft(
        self,
        external_event_id: str,
        payload: NexusCampaignDraftRequest,
        actor_id: str,
    ) -> tuple[Campaign, bool, ButtonType]:
        event = self.get(external_event_id)
        content = self.db.get(ContentItem, event.content_id) if event.content_id else None
        if content is None:
            raise DomainError(
                "WEBSITE_CONTENT_MISSING",
                "The imported website event has no Telegram content draft.",
                409,
            )
        action_url = event.payload.get("action_url")
        if not action_url:
            raise DomainError(
                "WEBSITE_ACTION_URL_REQUIRED",
                "A website action URL is required before creating a Campaign draft.",
                422,
            )
        TrackingService(self.db, get_settings()).validate_target_url(str(action_url))
        button_type, button_label = WEBSITE_PRIMARY_BUTTONS[content.content_type]

        existing = self.db.scalar(
            select(Campaign)
            .where(
                Campaign.content_id == content.id,
                Campaign.status.in_(
                    [
                        CampaignStatus.DRAFT,
                        CampaignStatus.WAITING_APPROVAL,
                        CampaignStatus.VALIDATION_FAILED,
                    ]
                ),
            )
            .order_by(Campaign.created_at)
        )
        if existing is not None:
            destination_ids = set(
                self.db.scalars(
                    select(CampaignDestination.destination_id).where(
                        CampaignDestination.campaign_id == existing.id
                    )
                ).all()
            )
            button = self.db.scalar(
                select(CampaignButton).where(CampaignButton.campaign_id == existing.id)
            )
            same = (
                destination_ids == set(payload.destination_ids)
                and existing.media_id == payload.media_id
                and existing.scheduled_at is not None
                and as_utc(existing.scheduled_at) == as_utc(payload.scheduled_at)
                and button is not None
                and button.button_type == button_type
                and button.target_url == str(action_url)
            )
            if same:
                return existing, True, button_type
            raise DomainError(
                "WEBSITE_CAMPAIGN_DRAFT_CONFLICT",
                "This website event already has a Campaign draft with different settings.",
                409,
                {"campaign_id": existing.id},
            )

        campaign = CampaignService(self.db).create(
            CampaignCreate(
                content_id=content.id,
                media_id=payload.media_id,
                destination_ids=payload.destination_ids,
                publish_mode=PublishMode.SCHEDULED,
                scheduled_at=payload.scheduled_at,
                created_by=actor_id,
                buttons=[
                    CampaignButtonCreate(
                        button_type=button_type,
                        label=button_label,
                        target_url=str(action_url),
                        row_number=0,
                        position=0,
                    )
                ],
            )
        )
        return campaign, False, button_type
