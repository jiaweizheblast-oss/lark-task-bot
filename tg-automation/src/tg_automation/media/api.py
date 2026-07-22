from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from tg_automation.core.api import success
from tg_automation.core.auth import Actor, require_operator
from tg_automation.media.schemas import MediaCreate, MediaRead
from tg_automation.media.service import MediaService
from tg_automation.storage.database import get_db
from tg_automation.storage.enums import RecordStatus

router = APIRouter(prefix="/tg/media", tags=["media-library"])


def serialize(item) -> dict:
    return MediaRead.model_validate(item).model_dump(mode="json")


@router.post("")
def create_media(
    payload: MediaCreate,
    actor: Actor = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    return success(serialize(MediaService(db).create(payload, actor.actor_id)))


@router.get("")
def list_media(
    status: RecordStatus | None = None,
    db: Session = Depends(get_db),
) -> dict:
    return success([serialize(item) for item in MediaService(db).list(status=status)])


@router.get("/recommend")
def recommend_media(db: Session = Depends(get_db)) -> dict:
    return success(serialize(MediaService(db).recommend()))


@router.post("/{media_id}/enable")
def enable_media(
    media_id: str,
    actor: Actor = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    return success(
        serialize(MediaService(db).set_status(media_id, RecordStatus.ENABLED, actor.actor_id))
    )


@router.post("/{media_id}/disable")
def disable_media(
    media_id: str,
    actor: Actor = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    return success(
        serialize(MediaService(db).set_status(media_id, RecordStatus.DISABLED, actor.actor_id))
    )
