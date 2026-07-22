from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tg_automation.core.audit import audit
from tg_automation.core.errors import DomainError, NotFoundError
from tg_automation.core.time import utc_now
from tg_automation.destinations.schemas import DestinationCreate, DestinationUpdate
from tg_automation.storage.enums import DestinationType, RecordStatus
from tg_automation.storage.models import TelegramDestination
from tg_automation.telegram.gateway import TelegramGateway
from tg_automation.telegram.schemas import TelegramSendResult, TestSendRequest


class DestinationService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self, payload: DestinationCreate, actor_id: str | None = None
    ) -> TelegramDestination:
        item = TelegramDestination(**payload.model_dump())
        self.db.add(item)
        try:
            self.db.flush()
        except IntegrityError as exc:
            self.db.rollback()
            raise DomainError(
                "DESTINATION_CONFLICT",
                "Telegram chat ID and source code must be unique.",
                409,
            ) from exc
        audit(
            self.db,
            actor_id=actor_id,
            action="DESTINATION_CREATED",
            resource_type="destination",
            resource_id=item.id,
            after={"source_code": item.source_code, "is_test": item.is_test},
        )
        self._commit_unique()
        self.db.refresh(item)
        return item

    def get(self, destination_id: str) -> TelegramDestination:
        item = self.db.get(TelegramDestination, destination_id)
        if item is None:
            raise NotFoundError("destination", destination_id)
        return item

    def list(
        self,
        *,
        status: RecordStatus | None = None,
        destination_type: DestinationType | None = None,
        is_test: bool | None = None,
    ) -> list[TelegramDestination]:
        query = select(TelegramDestination)
        if status is not None:
            query = query.where(TelegramDestination.status == status)
        if destination_type is not None:
            query = query.where(TelegramDestination.destination_type == destination_type)
        if is_test is not None:
            query = query.where(TelegramDestination.is_test == is_test)
        return list(self.db.scalars(query.order_by(TelegramDestination.name)).all())

    def update(
        self, destination_id: str, payload: DestinationUpdate, actor_id: str | None = None
    ) -> TelegramDestination:
        item = self.get(destination_id)
        changes = payload.model_dump(exclude_unset=True)
        if not changes:
            return item
        current = {
            "name": item.name,
            "telegram_chat_id": item.telegram_chat_id,
            "destination_type": item.destination_type,
            "source_code": item.source_code,
            "is_test": item.is_test,
        }
        validated = DestinationCreate.model_validate({**current, **changes})
        before = {field: str(getattr(item, field)) for field in changes}
        connection_changed = bool({"telegram_chat_id", "destination_type"}.intersection(changes))
        for field in changes:
            setattr(item, field, getattr(validated, field))
        if connection_changed:
            item.bot_can_post = False
            item.last_permission_check = None
        audit(
            self.db,
            actor_id=actor_id,
            action="DESTINATION_UPDATED",
            resource_type="destination",
            resource_id=item.id,
            before=before,
            after={"changed_fields": sorted(changes)},
        )
        self._commit_unique()
        self.db.refresh(item)
        return item

    def set_status(
        self, destination_id: str, status: RecordStatus, actor_id: str | None = None
    ) -> TelegramDestination:
        item = self.get(destination_id)
        previous = item.status
        item.status = status
        audit(
            self.db,
            actor_id=actor_id,
            action="DESTINATION_STATUS_CHANGED",
            resource_type="destination",
            resource_id=item.id,
            before={"status": previous.value},
            after={"status": status.value},
        )
        self.db.commit()
        self.db.refresh(item)
        return item

    def set_bulk_status(
        self,
        destination_ids: list[str],
        status: RecordStatus,
        actor_id: str | None = None,
    ) -> list[TelegramDestination]:
        items = list(
            self.db.scalars(
                select(TelegramDestination).where(TelegramDestination.id.in_(destination_ids))
            ).all()
        )
        if len(items) != len(destination_ids):
            found = {item.id for item in items}
            raise DomainError(
                "DESTINATION_NOT_FOUND",
                "One or more destinations do not exist.",
                404,
                {"destination_ids": [item for item in destination_ids if item not in found]},
            )
        for item in items:
            previous = item.status
            item.status = status
            audit(
                self.db,
                actor_id=actor_id,
                action="DESTINATION_STATUS_CHANGED",
                resource_type="destination",
                resource_id=item.id,
                before={"status": previous.value},
                after={"status": status.value, "bulk": True},
            )
        self.db.commit()
        return items

    async def check_permissions(
        self,
        destination_id: str,
        gateway: TelegramGateway,
        actor_id: str | None = None,
    ) -> TelegramDestination:
        item = self.get(destination_id)
        result = await gateway.check_permissions(item.telegram_chat_id)
        configured = item.telegram_chat_id.strip()
        if configured.lstrip("-").isdigit() and configured != result.chat_id:
            raise DomainError(
                "DESTINATION_ID_MISMATCH",
                "Telegram returned a different destination ID.",
                409,
            )
        if not configured.lstrip("-").isdigit():
            item.telegram_chat_id = result.chat_id
        item.bot_can_post = result.can_post
        item.last_permission_check = utc_now()
        audit(
            self.db,
            actor_id=actor_id,
            action="DESTINATION_PERMISSIONS_CHECKED",
            resource_type="destination",
            resource_id=item.id,
            after={
                "can_post": result.can_post,
                "chat_type": result.chat_type,
            },
        )
        self._commit_unique()
        self.db.refresh(item)
        return item

    async def send_test(
        self,
        destination_id: str,
        payload: TestSendRequest,
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
        item = self.get(destination_id)
        if not item.is_test:
            raise DomainError(
                "TEST_DESTINATION_REQUIRED",
                "Test messages may only be sent to test destinations.",
                422,
            )
        if item.status != RecordStatus.ENABLED:
            raise DomainError("DESTINATION_DISABLED", "Destination is disabled.", 422)
        result = await gateway.send_photo(
            item.telegram_chat_id, payload.photo, payload.caption, payload.buttons
        )
        item.bot_can_post = True
        item.last_permission_check = utc_now()
        audit(
            self.db,
            actor_id=actor_id,
            action="TEST_MESSAGE_SENT",
            resource_type="destination",
            resource_id=item.id,
            after={"telegram_message_id": result.message_id},
        )
        self.db.commit()
        return result

    def _commit_unique(self) -> None:
        try:
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise DomainError(
                "DESTINATION_CONFLICT",
                "Telegram chat ID and source code must be unique.",
                409,
            ) from exc
