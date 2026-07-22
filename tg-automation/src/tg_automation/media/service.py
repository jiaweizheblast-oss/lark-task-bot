from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from tg_automation.core.audit import audit
from tg_automation.core.errors import NotFoundError
from tg_automation.media.schemas import MediaCreate
from tg_automation.storage.enums import RecordStatus
from tg_automation.storage.models import MediaAsset


class MediaService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, payload: MediaCreate, actor_id: str | None = None) -> MediaAsset:
        item = MediaAsset(**payload.model_dump())
        self.db.add(item)
        self.db.flush()
        audit(
            self.db,
            actor_id=actor_id,
            action="MEDIA_CREATED",
            resource_type="media",
            resource_id=item.id,
            after={"name": item.name},
        )
        self.db.commit()
        self.db.refresh(item)
        return item

    def list(
        self,
        *,
        status: RecordStatus | None = None,
    ) -> list[MediaAsset]:
        query = select(MediaAsset)
        if status is not None:
            query = query.where(MediaAsset.status == status)
        return list(self.db.scalars(query.order_by(MediaAsset.created_at.desc())).all())

    def get(self, media_id: str) -> MediaAsset:
        item = self.db.get(MediaAsset, media_id)
        if item is None:
            raise NotFoundError("media", media_id)
        return item

    def set_status(
        self, media_id: str, status: RecordStatus, actor_id: str | None = None
    ) -> MediaAsset:
        item = self.get(media_id)
        previous = item.status.value
        item.status = status
        audit(
            self.db,
            actor_id=actor_id,
            action="MEDIA_STATUS_CHANGED",
            resource_type="media",
            resource_id=item.id,
            before={"status": previous},
            after={"status": status.value},
        )
        self.db.commit()
        self.db.refresh(item)
        return item

    def recommend(self, _active_at: datetime | None = None) -> MediaAsset:
        item = self.db.scalar(
            select(MediaAsset)
            .where(MediaAsset.status == RecordStatus.ENABLED)
            .order_by(
                MediaAsset.last_used_at.asc().nulls_first(),
                MediaAsset.usage_count,
                MediaAsset.created_at,
            )
            .limit(1)
        )
        if item is None:
            raise NotFoundError("media recommendation", "enabled image")
        return item
