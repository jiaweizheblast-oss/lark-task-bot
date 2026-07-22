from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from tg_automation.content.schemas import ContentCreate, ContentUpdate
from tg_automation.core.audit import audit
from tg_automation.core.errors import DomainError, NotFoundError
from tg_automation.storage.enums import ContentStatus
from tg_automation.storage.models import ContentItem


class ContentService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, payload: ContentCreate) -> ContentItem:
        item = ContentItem(**payload.model_dump())
        self.db.add(item)
        self.db.flush()
        audit(
            self.db,
            actor_id=payload.created_by,
            action="CONTENT_CREATED",
            resource_type="content",
            resource_id=item.id,
            after={"content_type": item.content_type.value, "status": item.status.value},
        )
        self.db.commit()
        self.db.refresh(item)
        return item

    def list(self) -> list[ContentItem]:
        return list(
            self.db.scalars(select(ContentItem).order_by(ContentItem.created_at.desc())).all()
        )

    def get(self, content_id: str) -> ContentItem:
        item = self.db.get(ContentItem, content_id)
        if item is None:
            raise NotFoundError("content", content_id)
        return item

    def update(
        self, content_id: str, payload: ContentUpdate, actor_id: str | None = None
    ) -> ContentItem:
        item = self.get(content_id)
        if item.status not in {ContentStatus.DRAFT, ContentStatus.WAITING_REVIEW}:
            raise DomainError(
                "CONTENT_NOT_EDITABLE",
                "Approved or archived content cannot be edited.",
                409,
            )
        changes = payload.model_dump(exclude_unset=True)
        if not changes:
            return item
        current = {
            "content_type": item.content_type,
            "title": item.title,
            "caption": item.caption,
            "valid_from": item.valid_from,
            "valid_until": item.valid_until,
            "source_type": item.source_type,
            "source_reference": item.source_reference,
            "language": item.language,
            "created_by": item.created_by,
        }
        validated = ContentCreate.model_validate({**current, **changes})
        before = {field: getattr(item, field) for field in changes}
        for field in changes:
            setattr(item, field, getattr(validated, field))
        audit(
            self.db,
            actor_id=actor_id,
            action="CONTENT_UPDATED",
            resource_type="content",
            resource_id=item.id,
            before={
                field: str(value) if value is not None else None for field, value in before.items()
            },
            after={
                field: str(getattr(item, field)) if getattr(item, field) is not None else None
                for field in changes
            },
        )
        self.db.commit()
        self.db.refresh(item)
        return item

    def approve(self, content_id: str, actor_id: str | None = None) -> ContentItem:
        item = self.get(content_id)
        if item.status not in {ContentStatus.DRAFT, ContentStatus.WAITING_REVIEW}:
            raise DomainError(
                "CONTENT_NOT_APPROVABLE",
                "Only draft or waiting-review content can be approved.",
                409,
            )
        previous = item.status.value
        item.status = ContentStatus.APPROVED
        audit(
            self.db,
            actor_id=actor_id,
            action="CONTENT_APPROVED",
            resource_type="content",
            resource_id=item.id,
            before={"status": previous},
            after={"status": item.status.value},
        )
        self.db.commit()
        self.db.refresh(item)
        return item

    def archive(self, content_id: str, actor_id: str | None = None) -> ContentItem:
        item = self.get(content_id)
        previous = item.status.value
        item.status = ContentStatus.ARCHIVED
        audit(
            self.db,
            actor_id=actor_id,
            action="CONTENT_ARCHIVED",
            resource_type="content",
            resource_id=item.id,
            before={"status": previous},
            after={"status": item.status.value},
        )
        self.db.commit()
        self.db.refresh(item)
        return item
