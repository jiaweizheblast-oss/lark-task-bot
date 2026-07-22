from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from tg_automation.content.schemas import ContentCreate, ContentRead, ContentUpdate
from tg_automation.content.service import ContentService
from tg_automation.core.api import success
from tg_automation.core.auth import Actor, require_approver, require_operator
from tg_automation.storage.database import get_db

router = APIRouter(prefix="/tg/contents", tags=["content-centre"])


def serialize(item) -> dict:
    return ContentRead.model_validate(item).model_dump(mode="json")


@router.post("")
def create_content(
    payload: ContentCreate,
    actor: Actor = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    effective = payload.model_copy(update={"created_by": payload.created_by or actor.actor_id})
    return success(serialize(ContentService(db).create(effective)))


@router.get("")
def list_content(db: Session = Depends(get_db)) -> dict:
    return success([serialize(item) for item in ContentService(db).list()])


@router.get("/{content_id}")
def get_content(content_id: str, db: Session = Depends(get_db)) -> dict:
    return success(serialize(ContentService(db).get(content_id)))


@router.patch("/{content_id}")
def update_content(
    content_id: str,
    payload: ContentUpdate,
    actor: Actor = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    return success(serialize(ContentService(db).update(content_id, payload, actor.actor_id)))


@router.post("/{content_id}/approve")
def approve_content(
    content_id: str,
    actor: Actor = Depends(require_approver),
    db: Session = Depends(get_db),
) -> dict:
    return success(serialize(ContentService(db).approve(content_id, actor.actor_id)))


@router.post("/{content_id}/archive")
def archive_content(
    content_id: str,
    actor: Actor = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    return success(serialize(ContentService(db).archive(content_id, actor.actor_id)))
