from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tg_automation.core.time import as_utc, utc_now
from tg_automation.storage.enums import CampaignStatus, DeliveryStatus, RecordStatus
from tg_automation.storage.models import (
    Campaign,
    ContentItem,
    MessageDelivery,
    TelegramDestination,
)


class AdminBotService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def destinations_text(self) -> str:
        items = self.db.scalars(
            select(TelegramDestination)
            .where(TelegramDestination.status == RecordStatus.ENABLED)
            .order_by(TelegramDestination.is_test, TelegramDestination.name)
        ).all()
        if not items:
            return "👥 GROUPS & CHANNELS\n\nNo destinations are configured yet."
        lines = ["👥 GROUPS & CHANNELS", ""]
        permission_cutoff = utc_now() - timedelta(hours=24)
        for item in items[:20]:
            if item.is_test:
                marker = "🧪"
            elif (
                item.bot_can_post
                and item.last_permission_check is not None
                and as_utc(item.last_permission_check) >= permission_cutoff
            ):
                marker = "✅"
            else:
                marker = "⚠️"
            lines.append(f"{marker} {item.name} · {item.destination_type.value}")
        if len(items) > 20:
            lines.append(f"\n+{len(items) - 20} more — open NEXUS to view all.")
        return "\n".join(lines)

    def scheduled_text(self) -> str:
        now = utc_now()
        rows = self.db.execute(
            select(Campaign, ContentItem.title)
            .join(ContentItem, ContentItem.id == Campaign.content_id)
            .where(
                Campaign.status == CampaignStatus.SCHEDULED,
                Campaign.scheduled_at >= now,
            )
            .order_by(Campaign.scheduled_at)
            .limit(10)
        ).all()
        if not rows:
            return "🕒 SCHEDULED TASKS\n\nNo upcoming Campaigns."
        lines = ["🕒 SCHEDULED TASKS", ""]
        for campaign, title in rows:
            scheduled_ist = as_utc(campaign.scheduled_at).astimezone(ZoneInfo("Asia/Kolkata"))
            lines.append(f"• {scheduled_ist:%Y-%m-%d %H:%M IST} · {title}")
        return "\n".join(lines)

    def status_text(self) -> str:
        counts = dict(
            self.db.execute(
                select(MessageDelivery.status, func.count(MessageDelivery.id)).group_by(
                    MessageDelivery.status
                )
            ).all()
        )
        return "\n".join(
            [
                "📊 SENDING STATUS",
                "",
                f"Pending: {int(counts.get(DeliveryStatus.PENDING, 0))}",
                f"Sending: {int(counts.get(DeliveryStatus.SENDING, 0))}",
                f"Retrying: {int(counts.get(DeliveryStatus.RETRYING, 0))}",
                f"Sent: {int(counts.get(DeliveryStatus.SENT, 0))}",
                f"Failed: {int(counts.get(DeliveryStatus.FAILED, 0))}",
            ]
        )
