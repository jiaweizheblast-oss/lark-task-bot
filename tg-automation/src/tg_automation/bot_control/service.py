from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tg_automation.core.config import Settings
from tg_automation.storage.enums import CampaignStatus, DeliveryStatus, RecordStatus
from tg_automation.storage.models import Campaign, MessageDelivery, TelegramDestination


class BotControlService:
    def __init__(self, db: Session, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    def overview(self, include_test: bool = False) -> dict:
        destination_filter = [] if include_test else [TelegramDestination.is_test.is_(False)]
        destinations = int(
            self.db.scalar(
                select(func.count(TelegramDestination.id)).where(
                    TelegramDestination.status == RecordStatus.ENABLED,
                    *destination_filter,
                )
            )
            or 0
        )
        scheduled = int(
            self.db.scalar(
                select(func.count(Campaign.id)).where(Campaign.status == CampaignStatus.SCHEDULED)
            )
            or 0
        )
        delivery_counts = dict(
            self.db.execute(
                select(MessageDelivery.status, func.count(MessageDelivery.id))
                .join(
                    TelegramDestination,
                    TelegramDestination.id == MessageDelivery.destination_id,
                )
                .where(*destination_filter)
                .group_by(MessageDelivery.status)
            ).all()
        )
        return {
            "admin_bot": {
                "name": "TG Automation Bot",
                "username": self.settings.telegram_bot_username,
                "authorised_admin_count": len(self.settings.admin_user_ids),
                "menu": [
                    "CREATE_OR_SEND",
                    "SCHEDULED_TASKS",
                    "GROUPS_AND_CHANNELS",
                    "SENDING_STATUS",
                ],
            },
            "destinations": {"enabled": destinations},
            "campaigns": {"scheduled": scheduled},
            "deliveries": {
                status.value.lower(): int(delivery_counts.get(status, 0))
                for status in DeliveryStatus
            },
        }
