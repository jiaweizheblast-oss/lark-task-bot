from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from tg_automation.campaigns.schemas import CampaignButtonCreate, CampaignCreate
from tg_automation.campaigns.service import CampaignService
from tg_automation.content.schemas import ContentCreate
from tg_automation.content.service import ContentService
from tg_automation.core.api import success
from tg_automation.core.auth import Actor, require_operator
from tg_automation.core.config import get_settings
from tg_automation.core.errors import DomainError, NotFoundError
from tg_automation.core.time import as_utc, utc_now
from tg_automation.media.schemas import MediaCreate
from tg_automation.media.service import MediaService
from tg_automation.storage.database import get_db
from tg_automation.storage.enums import (
    ButtonType,
    CampaignStatus,
    ContentType,
    PublishMode,
    RecordStatus,
)
from tg_automation.storage.models import (
    Campaign,
    CampaignDestination,
    ContentItem,
    MessageDelivery,
    TelegramDestination,
)

router = APIRouter(prefix="/tg/test-schedules", tags=["test-schedules"])

MAX_IMAGE_BYTES = 10 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_SLOTS = {"09:00", "15:00", "21:00"}
IST = ZoneInfo("Asia/Kolkata")


def _validate_image(content_type: str | None, image: bytes) -> None:
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise DomainError("INVALID_IMAGE", "The image must be JPEG, PNG, or WebP.", 422)
    if not image:
        raise DomainError("EMPTY_IMAGE", "The image is empty.", 422)
    if len(image) > MAX_IMAGE_BYTES:
        raise DomainError("IMAGE_TOO_LARGE", "The image must be 10 MB or smaller.", 413)
    valid_signature = (
        image.startswith(b"\xff\xd8\xff")
        or image.startswith(b"\x89PNG\r\n\x1a\n")
        or (len(image) >= 12 and image[:4] == b"RIFF" and image[8:12] == b"WEBP")
    )
    if not valid_signature:
        raise DomainError("INVALID_IMAGE", "The uploaded file is not a valid image.", 422)


def _scheduled_at(schedule_date: date, slot: str) -> datetime:
    if slot not in ALLOWED_SLOTS:
        raise DomainError(
            "INVALID_TIME_SLOT",
            "Choose 09:00, 15:00, or 21:00 India time.",
            422,
        )
    hour, minute = (int(part) for part in slot.split(":"))
    value = datetime.combine(schedule_date, time(hour, minute), tzinfo=IST)
    utc_value = as_utc(value)
    if utc_value <= utc_now():
        raise DomainError("SCHEDULE_TIME_IN_PAST", "Choose a future India time slot.", 422)
    return utc_value


def _serialize_schedule(
    campaign: Campaign,
    destination: TelegramDestination,
    content: ContentItem,
    delivery: MessageDelivery | None,
) -> dict:
    scheduled = as_utc(campaign.scheduled_at).astimezone(IST) if campaign.scheduled_at else None
    return {
        "id": campaign.id,
        "campaign_code": campaign.campaign_code,
        "destination_id": destination.id,
        "destination_name": destination.name,
        "caption": content.caption,
        "scheduled_at": campaign.scheduled_at.isoformat() if campaign.scheduled_at else None,
        "schedule_date": scheduled.date().isoformat() if scheduled else None,
        "time_slot": scheduled.strftime("%H:%M") if scheduled else None,
        "timezone": "Asia/Kolkata",
        "status": campaign.status.value,
        "delivery_status": delivery.status.value if delivery else None,
        "telegram_message_id": delivery.telegram_message_id if delivery else None,
        "error_message": delivery.error_message if delivery else None,
    }


@router.post("")
async def create_test_schedule(
    destination_id: str = Form(min_length=1),
    caption: str = Form(min_length=1, max_length=1024),
    schedule_date: date = Form(),
    time_slot: str = Form(),
    button_label: str = Form(default="", max_length=64),
    button_url: str = Form(default="", max_length=2000),
    photo: UploadFile = File(),
    actor: Actor = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    settings = get_settings()
    if not settings.telegram_test_sending_enabled:
        raise DomainError(
            "TEST_SENDING_DISABLED",
            "Telegram test sending is disabled.",
            423,
        )
    destination = db.get(TelegramDestination, destination_id)
    if destination is None:
        raise NotFoundError("destination", destination_id)
    if not destination.is_test or destination.status != RecordStatus.ENABLED:
        raise DomainError(
            "TEST_DESTINATION_REQUIRED",
            "Scheduled tests may only use an enabled test destination.",
            422,
        )
    label = button_label.strip()
    target = button_url.strip()
    if bool(label) != bool(target):
        raise DomainError(
            "BUTTON_INCOMPLETE",
            "Button text and button link must be provided together.",
            422,
        )
    scheduled_at = _scheduled_at(schedule_date, time_slot)
    existing_campaign_id = db.scalar(
        select(Campaign.id)
        .join(CampaignDestination, CampaignDestination.campaign_id == Campaign.id)
        .where(
            CampaignDestination.destination_id == destination.id,
            Campaign.scheduled_at == scheduled_at,
            Campaign.status.in_(
                [
                    CampaignStatus.SCHEDULED,
                    CampaignStatus.SENDING,
                    CampaignStatus.SENT,
                ]
            ),
        )
        .limit(1)
    )
    if existing_campaign_id:
        raise DomainError(
            "TEST_SLOT_ALREADY_SCHEDULED",
            "This test destination already has a message in that India time slot.",
            409,
            {"campaign_id": existing_campaign_id},
        )
    image = await photo.read(MAX_IMAGE_BYTES + 1)
    _validate_image(photo.content_type, image)

    storage_dir = settings.resolved_media_storage_dir
    storage_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(photo.filename or "image.jpg").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        suffix = ".jpg"
    image_path = storage_dir / f"scheduled-{uuid4().hex}{suffix}"
    image_path.write_bytes(image)

    title = next((line.strip() for line in caption.splitlines() if line.strip()), "TG message")
    content = ContentService(db).create(
        ContentCreate(
            content_type=ContentType.WEBSITE_ANNOUNCEMENT,
            title=title[:200],
            caption=caption.strip(),
            source_type="NEXUS_TEST_SCHEDULE",
            created_by=actor.actor_id,
        )
    )
    media = MediaService(db).create(
        MediaCreate(name=photo.filename or image_path.name, file_path=str(image_path)),
        actor.actor_id,
    )
    buttons = []
    if label and target:
        buttons.append(
            CampaignButtonCreate(
                button_type=ButtonType.VIEW_DETAILS,
                label=label,
                target_url=target,
                row_number=0,
                position=0,
            )
        )
    campaign_service = CampaignService(db)
    campaign = campaign_service.create(
        CampaignCreate(
            content_id=content.id,
            media_id=media.id,
            destination_ids=[destination.id],
            publish_mode=PublishMode.SCHEDULED,
            scheduled_at=scheduled_at,
            buttons=buttons,
            created_by=actor.actor_id,
        )
    )
    campaign = campaign_service.approve_and_schedule(campaign.id, actor.actor_id)
    delivery = db.scalar(
        select(MessageDelivery).where(MessageDelivery.campaign_id == campaign.id)
    )
    return success(_serialize_schedule(campaign, destination, content, delivery))


@router.get("")
def list_test_schedules(db: Session = Depends(get_db)) -> dict:
    rows = db.execute(
        select(Campaign, TelegramDestination, ContentItem, MessageDelivery)
        .join(ContentItem, ContentItem.id == Campaign.content_id)
        .join(CampaignDestination, CampaignDestination.campaign_id == Campaign.id)
        .join(
            TelegramDestination,
            TelegramDestination.id == CampaignDestination.destination_id,
        )
        .outerjoin(
            MessageDelivery,
            (MessageDelivery.campaign_id == Campaign.id)
            & (MessageDelivery.destination_id == TelegramDestination.id),
        )
        .where(
            TelegramDestination.is_test.is_(True),
            Campaign.status.in_(
                [
                    CampaignStatus.SCHEDULED,
                    CampaignStatus.SENDING,
                    CampaignStatus.SENT,
                    CampaignStatus.FAILED,
                ]
            ),
        )
        .order_by(Campaign.scheduled_at.desc())
        .limit(20)
    ).all()
    return success(
        [
            _serialize_schedule(campaign, destination, content, delivery)
            for campaign, destination, content, delivery in rows
        ]
    )
