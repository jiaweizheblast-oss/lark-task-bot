from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tg_automation.campaigns.renderer import render_buttons, render_caption
from tg_automation.campaigns.schemas import CampaignCreate, CampaignPreview, CampaignUpdate
from tg_automation.content.service import ContentService
from tg_automation.core.audit import audit
from tg_automation.core.config import get_settings
from tg_automation.core.errors import DomainError, NotFoundError
from tg_automation.core.time import as_utc, utc_now
from tg_automation.media.service import MediaService
from tg_automation.storage.enums import (
    CampaignStatus,
    ContentStatus,
    DeliveryStatus,
    PublishMode,
    RecordStatus,
)
from tg_automation.storage.models import (
    Campaign,
    CampaignButton,
    CampaignDestination,
    ContentItem,
    MediaAsset,
    MessageDelivery,
    TelegramDestination,
)
from tg_automation.telegram.gateway import TelegramGateway
from tg_automation.telegram.schemas import (
    TelegramButton,
    TelegramSendResult,
)
from tg_automation.tracking.service import TrackingService

PERMISSION_MAX_AGE = timedelta(hours=24)


def make_campaign_code() -> str:
    return f"TG-{utc_now():%Y%m%d}-{secrets.token_hex(4).upper()}"


def make_tracking_code() -> str:
    return f"tg_{secrets.token_urlsafe(10)}"


class CampaignService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, payload: CampaignCreate) -> Campaign:
        content = self.db.get(ContentItem, payload.content_id)
        if content is None:
            raise NotFoundError("content", payload.content_id)
        if payload.media_id and self.db.get(MediaAsset, payload.media_id) is None:
            raise NotFoundError("media", payload.media_id)

        destinations = list(
            self.db.scalars(
                select(TelegramDestination).where(
                    TelegramDestination.id.in_(set(payload.destination_ids))
                )
            ).all()
        )
        if len(destinations) != len(set(payload.destination_ids)):
            raise DomainError(
                "DESTINATION_NOT_FOUND",
                "One or more Telegram destinations do not exist.",
                404,
            )
        disabled = [item.id for item in destinations if item.status != RecordStatus.ENABLED]
        if disabled:
            raise DomainError(
                "DESTINATION_DISABLED",
                "Disabled destinations cannot be selected.",
                422,
                {"destination_ids": disabled},
            )

        campaign = Campaign(
            campaign_code=make_campaign_code(),
            content_id=payload.content_id,
            media_id=payload.media_id,
            publish_mode=payload.publish_mode,
            scheduled_at=payload.scheduled_at,
            display_timezone=payload.display_timezone,
            created_by=payload.created_by,
        )
        self.db.add(campaign)
        self.db.flush()

        for destination in destinations:
            self.db.add(
                CampaignDestination(
                    campaign_id=campaign.id,
                    destination_id=destination.id,
                    placement_code=destination.source_code,
                    tracking_code=make_tracking_code(),
                )
            )
        for button in payload.buttons:
            self.db.add(CampaignButton(campaign_id=campaign.id, **button.model_dump()))

        audit(
            self.db,
            actor_id=payload.created_by,
            action="CAMPAIGN_CREATED",
            resource_type="campaign",
            resource_id=campaign.id,
            after={"status": campaign.status.value, "campaign_code": campaign.campaign_code},
        )

        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise DomainError(
                "CAMPAIGN_CONFLICT",
                "Campaign could not be created because a unique value was duplicated.",
                409,
            ) from exc
        self.db.refresh(campaign)
        return campaign

    def get(self, campaign_id: str) -> Campaign:
        item = self.db.get(Campaign, campaign_id)
        if item is None:
            raise NotFoundError("campaign", campaign_id)
        return item

    def update(
        self, campaign_id: str, payload: CampaignUpdate, actor_id: str | None = None
    ) -> Campaign:
        campaign = self.get(campaign_id)
        if campaign.status not in {
            CampaignStatus.DRAFT,
            CampaignStatus.WAITING_APPROVAL,
            CampaignStatus.VALIDATION_FAILED,
        }:
            raise DomainError(
                "CAMPAIGN_NOT_EDITABLE",
                "Approved, scheduled, or completed Campaigns cannot be edited.",
                409,
            )
        changes = payload.model_dump(exclude_unset=True)
        if not changes:
            return campaign

        content_id = changes.get("content_id", campaign.content_id)
        if self.db.get(ContentItem, content_id) is None:
            raise NotFoundError("content", content_id)
        if (
            "media_id" in changes
            and changes["media_id"] is not None
            and self.db.get(MediaAsset, changes["media_id"]) is None
        ):
            raise NotFoundError("media", changes["media_id"])

        destination_ids = changes.pop("destination_ids", None)
        buttons = changes.pop("buttons", None)
        publish_mode = changes.get("publish_mode", campaign.publish_mode)
        scheduled_at = changes.get("scheduled_at", campaign.scheduled_at)
        if publish_mode == PublishMode.SCHEDULED and scheduled_at is None:
            raise DomainError(
                "SCHEDULE_TIME_REQUIRED",
                "Scheduled Campaigns require a send time.",
                422,
            )

        if destination_ids is not None:
            destinations = list(
                self.db.scalars(
                    select(TelegramDestination).where(
                        TelegramDestination.id.in_(set(destination_ids))
                    )
                ).all()
            )
            if len(destinations) != len(set(destination_ids)):
                raise DomainError(
                    "DESTINATION_NOT_FOUND",
                    "One or more Telegram destinations do not exist.",
                    404,
                )
            disabled = [
                destination.id
                for destination in destinations
                if destination.status != RecordStatus.ENABLED
            ]
            if disabled:
                raise DomainError(
                    "DESTINATION_DISABLED",
                    "Disabled destinations cannot be selected.",
                    422,
                    {"destination_ids": disabled},
                )
        else:
            destinations = []

        before = {
            field: str(getattr(campaign, field)) if getattr(campaign, field) is not None else None
            for field in changes
        }
        for field, value in changes.items():
            setattr(campaign, field, value)
        campaign.rendered_caption = None
        campaign.rendered_buttons = None
        campaign.rendered_at = None
        campaign.approved_by = None
        campaign.approved_at = None

        if destination_ids is not None:
            self.db.query(CampaignDestination).filter(
                CampaignDestination.campaign_id == campaign.id
            ).delete(synchronize_session=False)
            self.db.flush()
            for destination in destinations:
                self.db.add(
                    CampaignDestination(
                        campaign_id=campaign.id,
                        destination_id=destination.id,
                        placement_code=destination.source_code,
                        tracking_code=make_tracking_code(),
                    )
                )
        if buttons is not None:
            self.db.query(CampaignButton).filter(CampaignButton.campaign_id == campaign.id).delete(
                synchronize_session=False
            )
            self.db.flush()
            for button in buttons:
                self.db.add(CampaignButton(campaign_id=campaign.id, **button))

        audit(
            self.db,
            actor_id=actor_id,
            action="CAMPAIGN_UPDATED",
            resource_type="campaign",
            resource_id=campaign.id,
            before=before,
            after={"changed_fields": sorted(payload.model_fields_set)},
        )
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise DomainError(
                "CAMPAIGN_CONFLICT",
                "Campaign update conflicts with an existing value.",
                409,
            ) from exc
        self.db.refresh(campaign)
        return campaign

    def list(self) -> list[Campaign]:
        return list(self.db.scalars(select(Campaign).order_by(Campaign.created_at.desc())).all())

    def preview(self, campaign_id: str) -> CampaignPreview:
        campaign = self.get(campaign_id)
        content = self.db.get(ContentItem, campaign.content_id)
        if content is None:
            raise NotFoundError("content", campaign.content_id)
        media = self._resolve_media(campaign, content)
        buttons = list(
            self.db.scalars(
                select(CampaignButton).where(CampaignButton.campaign_id == campaign.id)
            ).all()
        )
        destinations = self.db.scalar(
            select(CampaignDestination)
            .where(CampaignDestination.campaign_id == campaign.id)
            .limit(1)
        )
        count = len(
            self.db.scalars(
                select(CampaignDestination).where(CampaignDestination.campaign_id == campaign.id)
            ).all()
        )
        if destinations is None:
            raise DomainError("CAMPAIGN_HAS_NO_DESTINATIONS", "Campaign has no destinations.", 422)
        return CampaignPreview(
            campaign_id=campaign.id,
            campaign_code=campaign.campaign_code,
            caption=render_caption(content),
            media_id=media.id,
            photo=media.telegram_file_id or media.file_path,
            buttons=render_buttons(buttons),
            destination_count=count,
        )

    def preflight(self, campaign_id: str, scheduled_at: datetime | None = None) -> dict:
        campaign = self.get(campaign_id)
        dispatch_at = as_utc(scheduled_at or campaign.scheduled_at or utc_now())
        checks: list[dict] = []

        def add(name: str, passed: bool, message: str, details: dict | None = None) -> None:
            checks.append(
                {
                    "name": name,
                    "passed": passed,
                    "message": message,
                    "details": details or {},
                }
            )

        add(
            "campaign_approved",
            campaign.status == CampaignStatus.APPROVED,
            "Campaign is approved."
            if campaign.status == CampaignStatus.APPROVED
            else "Campaign still requires approval.",
        )
        content = self.db.get(ContentItem, campaign.content_id)
        content_approved = bool(content and content.status == ContentStatus.APPROVED)
        add(
            "content_approved",
            content_approved,
            "Content is approved." if content_approved else "Content is not approved.",
        )
        content_active = bool(
            content
            and (not content.valid_from or dispatch_at >= as_utc(content.valid_from))
            and (not content.valid_until or dispatch_at < as_utc(content.valid_until))
        )
        add(
            "content_active",
            content_active,
            "Content is active at the planned send time."
            if content_active
            else "Content is outside its validity window.",
            {"dispatch_at": dispatch_at.isoformat()},
        )

        preview = None
        try:
            preview = self.preview(campaign_id)
        except DomainError as exc:
            add("message_render", False, exc.message, {"code": exc.code})
        else:
            add(
                "message_render",
                True,
                "Caption, image, and buttons can be rendered.",
                {"media_id": preview.media_id, "caption_length": len(preview.caption)},
            )

        links = list(
            self.db.scalars(
                select(CampaignDestination).where(CampaignDestination.campaign_id == campaign.id)
            ).all()
        )
        unavailable: list[str] = []
        permission_missing: list[str] = []
        permission_stale: list[str] = []
        for link in links:
            destination = self.db.get(TelegramDestination, link.destination_id)
            if destination is None or destination.status != RecordStatus.ENABLED:
                unavailable.append(link.destination_id)
                continue
            if destination.is_test:
                continue
            if not destination.bot_can_post:
                permission_missing.append(destination.id)
            elif (
                destination.last_permission_check is None
                or as_utc(destination.last_permission_check) < utc_now() - PERMISSION_MAX_AGE
            ):
                permission_stale.append(destination.id)
        add(
            "destinations_available",
            bool(links) and not unavailable,
            "All destinations are enabled."
            if links and not unavailable
            else "One or more destinations are unavailable.",
            {"destination_ids": unavailable},
        )
        add(
            "posting_permissions",
            not permission_missing and not permission_stale,
            "Posting permission is current for every real destination."
            if not permission_missing and not permission_stale
            else "Posting permission is missing or stale.",
            {"missing": permission_missing, "stale": permission_stale},
        )

        links_valid = preview is not None
        link_error: dict = {}
        if preview is not None:
            tracking = TrackingService(self.db, get_settings())
            try:
                for button in preview.buttons:
                    tracking.validate_target_url(str(button["value"]))
            except DomainError as exc:
                links_valid = False
                link_error = {"code": exc.code}
                link_message = exc.message
            else:
                link_message = "All outbound links use approved destinations."
        else:
            link_message = "Links cannot be checked until the message renders."
        add("outbound_links", links_valid, link_message, link_error)

        configuration_checks = [item for item in checks if item["name"] != "campaign_approved"]
        return {
            "campaign_id": campaign.id,
            "dispatch_at": dispatch_at.isoformat(),
            "configuration_ready": all(item["passed"] for item in configuration_checks),
            "dispatch_ready": all(item["passed"] for item in checks),
            "checks": checks,
        }

    async def send_test_preview(
        self,
        campaign_id: str,
        destination_id: str,
        gateway: TelegramGateway,
        *,
        test_sending_enabled: bool,
        actor_id: str | None = None,
    ) -> TelegramSendResult:
        if not test_sending_enabled:
            raise DomainError(
                "TEST_SENDING_DISABLED",
                "Telegram test sending is disabled.",
                423,
            )
        destination = self.db.get(TelegramDestination, destination_id)
        if destination is None:
            raise NotFoundError("destination", destination_id)
        if not destination.is_test:
            raise DomainError(
                "TEST_DESTINATION_REQUIRED",
                "Campaign previews may only be sent to test destinations.",
                422,
            )
        if destination.status != RecordStatus.ENABLED:
            raise DomainError("DESTINATION_DISABLED", "Destination is disabled.", 422)
        campaign = self.get(campaign_id)
        preview = self.preview(campaign_id)
        if (
            campaign.rendered_at is not None
            and campaign.rendered_caption
            and campaign.rendered_buttons is not None
            and campaign.media_id
        ):
            media = self.db.get(MediaAsset, campaign.media_id)
            if media is None:
                raise NotFoundError("media", campaign.media_id)
            preview = preview.model_copy(
                update={
                    "caption": campaign.rendered_caption,
                    "buttons": campaign.rendered_buttons,
                    "media_id": media.id,
                    "photo": media.telegram_file_id or media.file_path,
                }
            )
        tracking = TrackingService(self.db, get_settings())
        buttons: list[TelegramButton] = []
        for item in preview.buttons:
            tracking.validate_target_url(str(item["value"]))
            buttons.append(
                TelegramButton(
                    label=item["label"],
                    value=str(item["value"]),
                    row=item["row"],
                    position=item["position"],
                )
            )
        result = await gateway.send_photo(
            destination.telegram_chat_id,
            preview.photo,
            preview.caption,
            buttons,
        )
        audit(
            self.db,
            actor_id=actor_id,
            action="CAMPAIGN_TEST_PREVIEW_SENT",
            resource_type="campaign",
            resource_id=campaign_id,
            after={
                "destination_id": destination.id,
                "telegram_message_id": result.message_id,
            },
        )
        self.db.commit()
        return result

    def approve(self, campaign_id: str, approved_by: str) -> Campaign:
        campaign = self.get(campaign_id)
        previous_status = campaign.status.value
        if campaign.status not in {
            CampaignStatus.DRAFT,
            CampaignStatus.WAITING_APPROVAL,
            CampaignStatus.VALIDATION_FAILED,
        }:
            raise DomainError(
                "CAMPAIGN_NOT_APPROVABLE",
                "Campaign is not in an approvable state.",
                409,
            )
        content = self.db.get(ContentItem, campaign.content_id)
        if content is None or content.status != ContentStatus.APPROVED:
            raise DomainError(
                "CONTENT_NOT_APPROVED",
                "Campaign content must be approved first.",
                422,
            )

        preview = self.preview(campaign_id)
        tracking = TrackingService(self.db, get_settings())
        for button in preview.buttons:
            tracking.validate_target_url(str(button["value"]))
        campaign.media_id = preview.media_id
        campaign.rendered_caption = preview.caption
        campaign.rendered_buttons = preview.buttons
        campaign.rendered_at = utc_now()
        campaign.status = CampaignStatus.APPROVED
        campaign.approved_by = approved_by
        campaign.approved_at = utc_now()
        media = self.db.get(MediaAsset, preview.media_id)
        if media is not None:
            media.last_used_at = campaign.approved_at
            media.usage_count += 1
        audit(
            self.db,
            actor_id=approved_by,
            action="CAMPAIGN_APPROVED",
            resource_type="campaign",
            resource_id=campaign.id,
            before={"status": previous_status},
            after={"status": campaign.status.value},
        )
        self.db.commit()
        self.db.refresh(campaign)
        return campaign

    def schedule(
        self, campaign_id: str, scheduled_at: datetime, actor_id: str | None = None
    ) -> Campaign:
        campaign = self.get(campaign_id)
        if campaign.status != CampaignStatus.APPROVED:
            raise DomainError(
                "CAMPAIGN_NOT_APPROVED",
                "Campaign must be approved before scheduling.",
                409,
            )
        when = as_utc(scheduled_at)
        if when <= utc_now():
            raise DomainError(
                "SCHEDULE_TIME_IN_PAST",
                "Scheduled time must be in the future.",
                422,
            )
        self._validate_dispatch(campaign, when)
        campaign.scheduled_at = when
        campaign.publish_mode = PublishMode.SCHEDULED
        campaign.status = CampaignStatus.SCHEDULED
        self._create_deliveries(campaign)
        audit(
            self.db,
            actor_id=actor_id,
            action="CAMPAIGN_SCHEDULED",
            resource_type="campaign",
            resource_id=campaign.id,
            after={"scheduled_at": when.isoformat()},
        )
        self.db.commit()
        self.db.refresh(campaign)
        return campaign

    def approve_and_schedule(self, campaign_id: str, actor_id: str) -> Campaign:
        """One explicit NEXUS action that keeps all existing approval checks."""

        campaign = self.get(campaign_id)
        if campaign.status not in {
            CampaignStatus.DRAFT,
            CampaignStatus.WAITING_APPROVAL,
            CampaignStatus.VALIDATION_FAILED,
        }:
            raise DomainError(
                "CAMPAIGN_NOT_FINALISABLE",
                "Only a reviewable Campaign draft can be approved and scheduled.",
                409,
            )
        if campaign.scheduled_at is None:
            raise DomainError(
                "SCHEDULE_TIME_REQUIRED",
                "A planned send time is required before final approval.",
                422,
            )
        report = self.preflight(campaign.id, campaign.scheduled_at)
        allowed_review_checks = {"campaign_approved", "content_approved"}
        blockers = [
            item
            for item in report["checks"]
            if not item["passed"] and item["name"] not in allowed_review_checks
        ]
        if blockers:
            raise DomainError(
                "CAMPAIGN_PREFLIGHT_FAILED",
                "Campaign configuration must pass preflight before final approval.",
                422,
                {"checks": blockers},
            )
        content = self.db.get(ContentItem, campaign.content_id)
        if content is None:
            raise NotFoundError("content", campaign.content_id)
        if content.status != ContentStatus.APPROVED:
            ContentService(self.db).approve(content.id, actor_id)
        self.approve(campaign.id, actor_id)
        return self.schedule(campaign.id, campaign.scheduled_at, actor_id)

    def send_now(
        self,
        campaign_id: str,
        *,
        sending_enabled: bool,
        actor_id: str | None = None,
    ) -> Campaign:
        if not sending_enabled:
            raise DomainError(
                "SENDING_GLOBALLY_DISABLED",
                "Global sending is disabled.",
                423,
            )
        campaign = self.get(campaign_id)
        if campaign.status != CampaignStatus.APPROVED:
            raise DomainError(
                "CAMPAIGN_NOT_APPROVED",
                "Campaign must be approved before sending.",
                409,
            )
        dispatch_at = utc_now()
        self._validate_dispatch(campaign, dispatch_at)
        campaign.publish_mode = PublishMode.IMMEDIATE
        campaign.scheduled_at = dispatch_at
        campaign.status = CampaignStatus.SCHEDULED
        self._create_deliveries(campaign)
        audit(
            self.db,
            actor_id=actor_id,
            action="CAMPAIGN_SEND_NOW_REQUESTED",
            resource_type="campaign",
            resource_id=campaign.id,
        )
        self.db.commit()
        self.db.refresh(campaign)
        return campaign

    def cancel(self, campaign_id: str, actor_id: str | None = None) -> Campaign:
        campaign = self.get(campaign_id)
        if campaign.status not in {
            CampaignStatus.DRAFT,
            CampaignStatus.WAITING_APPROVAL,
            CampaignStatus.APPROVED,
            CampaignStatus.SCHEDULED,
        }:
            raise DomainError(
                "CAMPAIGN_NOT_CANCELLABLE",
                "Campaign can no longer be cancelled.",
                409,
            )
        campaign.status = CampaignStatus.CANCELLED
        campaign.cancelled_at = utc_now()
        self.db.query(MessageDelivery).filter(
            MessageDelivery.campaign_id == campaign.id,
            MessageDelivery.status.in_([DeliveryStatus.PENDING, DeliveryStatus.RETRYING]),
        ).update({MessageDelivery.status: DeliveryStatus.CANCELLED}, synchronize_session=False)
        audit(
            self.db,
            actor_id=actor_id,
            action="CAMPAIGN_CANCELLED",
            resource_type="campaign",
            resource_id=campaign.id,
        )
        self.db.commit()
        self.db.refresh(campaign)
        return campaign

    def _resolve_media(self, campaign: Campaign, content: ContentItem) -> MediaAsset:
        if campaign.media_id:
            media = self.db.get(MediaAsset, campaign.media_id)
            if media is None or media.status != RecordStatus.ENABLED:
                raise DomainError(
                    "CAMPAIGN_MEDIA_UNAVAILABLE",
                    "Selected campaign media is unavailable.",
                    422,
                )
            return media
        return MediaService(self.db).recommend(campaign.scheduled_at)

    def _validate_dispatch(self, campaign: Campaign, dispatch_at: datetime) -> None:
        when = as_utc(dispatch_at)
        content = self.db.get(ContentItem, campaign.content_id)
        if content is None:
            raise NotFoundError("content", campaign.content_id)
        if content.status != ContentStatus.APPROVED:
            raise DomainError("CONTENT_NOT_APPROVED", "Campaign content is not approved.", 422)
        if content.valid_from and when < as_utc(content.valid_from):
            raise DomainError(
                "CONTENT_NOT_ACTIVE_AT_SEND_TIME",
                "Content is not active at the requested send time.",
                422,
            )
        if content.valid_until and when >= as_utc(content.valid_until):
            raise DomainError(
                "CONTENT_EXPIRES_BEFORE_SEND",
                "Content expires before the requested send time.",
                422,
            )
        if campaign.media_id is None:
            raise DomainError("CAMPAIGN_MEDIA_MISSING", "Approved Campaign has no media.", 422)
        media = self.db.get(MediaAsset, campaign.media_id)
        if media is None or media.status != RecordStatus.ENABLED:
            raise DomainError("CAMPAIGN_MEDIA_UNAVAILABLE", "Campaign media is unavailable.", 422)
        links = self.db.scalars(
            select(CampaignDestination).where(CampaignDestination.campaign_id == campaign.id)
        ).all()
        if not links:
            raise DomainError("CAMPAIGN_HAS_NO_DESTINATIONS", "Campaign has no destinations.", 422)
        invalid: list[str] = []
        unverified: list[str] = []
        stale_permissions: list[str] = []
        for link in links:
            destination = self.db.get(TelegramDestination, link.destination_id)
            if destination is None or destination.status != RecordStatus.ENABLED:
                invalid.append(link.destination_id)
            elif not destination.is_test and not destination.bot_can_post:
                unverified.append(destination.id)
            elif not destination.is_test and (
                destination.last_permission_check is None
                or as_utc(destination.last_permission_check) < utc_now() - PERMISSION_MAX_AGE
            ):
                stale_permissions.append(destination.id)
        if invalid:
            raise DomainError(
                "DESTINATION_UNAVAILABLE",
                "One or more destinations are no longer enabled.",
                422,
                {"destination_ids": invalid},
            )
        if unverified:
            raise DomainError(
                "DESTINATION_PERMISSION_UNVERIFIED",
                "Bot posting permission must be verified before scheduling.",
                422,
                {"destination_ids": unverified},
            )
        if stale_permissions:
            raise DomainError(
                "DESTINATION_PERMISSION_STALE",
                "Bot posting permission must be checked again before scheduling.",
                422,
                {"destination_ids": stale_permissions},
            )

    def _create_deliveries(self, campaign: Campaign) -> None:
        destination_links = list(
            self.db.scalars(
                select(CampaignDestination).where(CampaignDestination.campaign_id == campaign.id)
            ).all()
        )
        existing = set(
            self.db.scalars(
                select(MessageDelivery.destination_id).where(
                    MessageDelivery.campaign_id == campaign.id
                )
            ).all()
        )
        for link in destination_links:
            if link.destination_id in existing:
                continue
            destination = self.db.get(TelegramDestination, link.destination_id)
            if destination is None:
                raise NotFoundError("destination", link.destination_id)
            self.db.add(
                MessageDelivery(
                    campaign_id=campaign.id,
                    destination_id=destination.id,
                    telegram_chat_id=destination.telegram_chat_id,
                    status=DeliveryStatus.PENDING,
                    next_attempt_at=campaign.scheduled_at,
                )
            )
