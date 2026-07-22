from __future__ import annotations

import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.orm import Session

from tg_automation.core.api import success
from tg_automation.core.auth import Actor, require_operator
from tg_automation.core.config import get_settings
from tg_automation.core.errors import DomainError
from tg_automation.destinations.schemas import (
    DestinationBulkStatusRequest,
    DestinationCreate,
    DestinationRead,
    DestinationUpdate,
)
from tg_automation.destinations.service import DestinationService
from tg_automation.storage.database import get_db
from tg_automation.storage.enums import DestinationType, RecordStatus
from tg_automation.telegram.gateway import OfficialTelegramGateway, TelegramGateway
from tg_automation.telegram.schemas import TelegramButton, TestSendRequest

router = APIRouter(prefix="/tg/destinations", tags=["telegram-destinations"])

MAX_TEST_IMAGE_BYTES = 10 * 1024 * 1024
ALLOWED_TEST_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
BUTTON_LIST_ADAPTER = TypeAdapter(list[TelegramButton])


def get_telegram_gateway() -> TelegramGateway:
    return OfficialTelegramGateway(get_settings())


def serialize(item) -> dict:
    return DestinationRead.model_validate(item).model_dump(mode="json")


@router.post("")
def create_destination(
    payload: DestinationCreate,
    actor: Actor = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    return success(serialize(DestinationService(db).create(payload, actor.actor_id)))


@router.get("")
def list_destinations(
    status: RecordStatus | None = None,
    destination_type: DestinationType | None = None,
    is_test: bool | None = None,
    db: Session = Depends(get_db),
) -> dict:
    return success(
        [
            serialize(item)
            for item in DestinationService(db).list(
                status=status, destination_type=destination_type, is_test=is_test
            )
        ]
    )


@router.patch("/{destination_id}")
def update_destination(
    destination_id: str,
    payload: DestinationUpdate,
    actor: Actor = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    return success(
        serialize(DestinationService(db).update(destination_id, payload, actor.actor_id))
    )


@router.post("/bulk-status")
def bulk_destination_status(
    payload: DestinationBulkStatusRequest,
    actor: Actor = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    items = DestinationService(db).set_bulk_status(
        payload.destination_ids, payload.status, actor.actor_id
    )
    return success([serialize(item) for item in items])


@router.post("/{destination_id}/enable")
def enable_destination(
    destination_id: str,
    actor: Actor = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    return success(
        serialize(
            DestinationService(db).set_status(destination_id, RecordStatus.ENABLED, actor.actor_id)
        )
    )


@router.post("/{destination_id}/disable")
def disable_destination(
    destination_id: str,
    actor: Actor = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    return success(
        serialize(
            DestinationService(db).set_status(destination_id, RecordStatus.DISABLED, actor.actor_id)
        )
    )


@router.post("/{destination_id}/check-permissions")
async def check_permissions(
    destination_id: str,
    actor: Actor = Depends(require_operator),
    db: Session = Depends(get_db),
    gateway: TelegramGateway = Depends(get_telegram_gateway),
) -> dict:
    item = await DestinationService(db).check_permissions(destination_id, gateway, actor.actor_id)
    return success(serialize(item))


@router.post("/{destination_id}/send-test")
async def send_test(
    destination_id: str,
    payload: TestSendRequest,
    actor: Actor = Depends(require_operator),
    db: Session = Depends(get_db),
    gateway: TelegramGateway = Depends(get_telegram_gateway),
) -> dict:
    result = await DestinationService(db).send_test(
        destination_id,
        payload,
        gateway,
        test_sending_enabled=get_settings().telegram_test_sending_enabled,
        actor_id=actor.actor_id,
    )
    return success(result.model_dump())


@router.post("/{destination_id}/send-test-upload")
async def send_test_upload(
    destination_id: str,
    caption: str = Form(min_length=1, max_length=1024),
    buttons: str = Form(default="[]"),
    photo: UploadFile = File(),
    actor: Actor = Depends(require_operator),
    db: Session = Depends(get_db),
    gateway: TelegramGateway = Depends(get_telegram_gateway),
) -> dict:
    """Receive one browser-uploaded image and send it only to a test destination."""
    if photo.content_type not in ALLOWED_TEST_IMAGE_TYPES:
        raise DomainError(
            "INVALID_TEST_IMAGE",
            "The test image must be JPEG, PNG, or WebP.",
            422,
        )
    try:
        parsed_buttons = BUTTON_LIST_ADAPTER.validate_python(json.loads(buttons))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise DomainError("INVALID_BUTTONS", "The button configuration is invalid.", 422) from exc
    if len(parsed_buttons) > 5:
        raise DomainError("TOO_MANY_BUTTONS", "A message may contain at most 5 buttons.", 422)

    image_bytes = await photo.read(MAX_TEST_IMAGE_BYTES + 1)
    if len(image_bytes) > MAX_TEST_IMAGE_BYTES:
        raise DomainError("TEST_IMAGE_TOO_LARGE", "The test image must be 10 MB or smaller.", 413)
    if not image_bytes:
        raise DomainError("EMPTY_TEST_IMAGE", "The test image is empty.", 422)

    suffix = Path(photo.filename or "image.jpg").suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        suffix = ".jpg"
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(prefix="tg-test-", suffix=suffix, delete=False) as temporary:
            temporary.write(image_bytes)
            temporary_path = Path(temporary.name)
        payload = TestSendRequest(
            photo=str(temporary_path),
            caption=caption,
            buttons=parsed_buttons,
        )
        result = await DestinationService(db).send_test(
            destination_id,
            payload,
            gateway,
            test_sending_enabled=get_settings().telegram_test_sending_enabled,
            actor_id=actor.actor_id,
        )
        return success(result.model_dump())
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
