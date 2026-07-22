from __future__ import annotations

from sqlalchemy import or_, select

from tg_automation.core.config import Settings
from tg_automation.destinations.schemas import DestinationCreate, DestinationUpdate
from tg_automation.destinations.service import DestinationService
from tg_automation.storage.database import get_session_factory
from tg_automation.storage.enums import DestinationType
from tg_automation.storage.models import TelegramDestination


def bootstrap_test_destinations(settings: Settings) -> None:
    configured = [
        (
            settings.telegram_test_channel_id.strip(),
            "test channel",
            "test_channel",
            DestinationType.TEST_CHANNEL,
        ),
        (
            settings.telegram_test_group_id.strip(),
            "test group",
            "test_group",
            DestinationType.TEST_GROUP,
        ),
    ]
    with get_session_factory()() as db:
        service = DestinationService(db)
        for chat_id, name, source_code, destination_type in configured:
            if not chat_id:
                continue
            existing = db.scalar(
                select(TelegramDestination).where(
                    or_(
                        TelegramDestination.source_code == source_code,
                        TelegramDestination.telegram_chat_id == chat_id,
                    )
                )
            )
            if existing is None:
                service.create(
                    DestinationCreate(
                        name=name,
                        telegram_chat_id=chat_id,
                        destination_type=destination_type,
                        source_code=source_code,
                        is_test=True,
                    ),
                    actor_id="environment-bootstrap",
                )
                continue
            service.update(
                existing.id,
                DestinationUpdate(
                    name=name,
                    telegram_chat_id=chat_id,
                    destination_type=destination_type,
                    source_code=source_code,
                    is_test=True,
                ),
                actor_id="environment-bootstrap",
            )
