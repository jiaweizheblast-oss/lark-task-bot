from __future__ import annotations

import secrets
from datetime import timedelta
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from tg_automation.core.config import Settings
from tg_automation.core.errors import DomainError, NotFoundError
from tg_automation.core.time import as_utc, utc_now
from tg_automation.storage.enums import RecordStatus
from tg_automation.storage.models import (
    Campaign,
    CampaignDestination,
    ContentItem,
    TrackingEvent,
    TrackingLink,
)


class TrackingService:
    def __init__(self, db: Session, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    def validate_target_url(self, target_url: str) -> None:
        parsed = urlsplit(target_url)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        local_development = (
            self.settings.app_env != "production"
            and hostname in {"localhost", "127.0.0.1"}
            and parsed.scheme == "http"
        )
        host_allowed = any(
            hostname == allowed or hostname.endswith(f".{allowed}")
            for allowed in self.settings.allowed_redirect_hosts
        )
        if not local_development and (parsed.scheme != "https" or not host_allowed):
            raise DomainError(
                "TRACKING_TARGET_NOT_ALLOWED",
                "Tracking target must use HTTPS and an approved host.",
                422,
            )
        if parsed.username or parsed.password:
            raise DomainError(
                "TRACKING_TARGET_NOT_ALLOWED",
                "Tracking targets may not contain URL credentials.",
                422,
            )

    def render_buttons_for_destination(
        self,
        campaign: Campaign,
        destination_id: str,
        snapshot: list[dict],
    ) -> list[dict]:
        link = self.db.scalar(
            select(CampaignDestination).where(
                CampaignDestination.campaign_id == campaign.id,
                CampaignDestination.destination_id == destination_id,
            )
        )
        if link is None:
            raise NotFoundError("campaign destination", destination_id)
        content = self.db.get(ContentItem, campaign.content_id)
        if content is None:
            raise NotFoundError("content", campaign.content_id)

        output: list[dict] = []
        for button in snapshot:
            rendered = dict(button)

            target = str(button["value"])
            self.validate_target_url(target)
            tracking_link = self._get_or_create_link(
                campaign,
                destination_id,
                str(button["button_id"]),
                target,
                content.valid_until,
            )
            rendered["value"] = (
                f"{self.settings.tracking_base_url.rstrip('/')}/{tracking_link.tracking_code}"
            )
            output.append(rendered)
        self.db.flush()
        return output

    def resolve(self, tracking_code: str) -> TrackingLink:
        link = self.db.scalar(
            select(TrackingLink).where(TrackingLink.tracking_code == tracking_code)
        )
        if link is None or link.status != RecordStatus.ENABLED:
            raise NotFoundError("tracking link", tracking_code)
        if link.expires_at and as_utc(link.expires_at) <= utc_now():
            raise DomainError(
                "TRACKING_LINK_EXPIRED",
                "This campaign link has expired.",
                410,
            )
        self.validate_target_url(link.target_url)
        return link

    def record_click(
        self,
        link: TrackingLink,
        anonymous_visitor_id: str,
        user_agent: str | None,
    ) -> TrackingEvent:
        repeat_since = utc_now() - timedelta(minutes=5)
        is_repeat = (
            self.db.scalar(
                select(TrackingEvent.id)
                .where(
                    TrackingEvent.tracking_link_id == link.id,
                    TrackingEvent.anonymous_visitor_id == anonymous_visitor_id,
                    TrackingEvent.occurred_at >= repeat_since,
                )
                .limit(1)
            )
            is not None
        )
        event = TrackingEvent(
            campaign_id=link.campaign_id,
            destination_id=link.destination_id,
            tracking_link_id=link.id,
            tracking_code=link.tracking_code,
            event_type="LINK_CLICK",
            anonymous_visitor_id=anonymous_visitor_id,
            event_data={
                "is_repeat_within_5m": is_repeat,
                "user_agent": (user_agent or "")[:300],
            },
        )
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        return event

    def _get_or_create_link(
        self,
        campaign: Campaign,
        destination_id: str,
        campaign_button_id: str,
        target_url: str,
        expires_at,
    ) -> TrackingLink:
        item = self.db.scalar(
            select(TrackingLink).where(
                TrackingLink.campaign_id == campaign.id,
                TrackingLink.destination_id == destination_id,
                TrackingLink.campaign_button_id == campaign_button_id,
            )
        )
        if item is not None:
            if item.target_url != target_url:
                raise DomainError(
                    "TRACKING_TARGET_IMMUTABLE",
                    "A rendered tracking target cannot be changed after creation.",
                    409,
                )
            return item
        item = TrackingLink(
            campaign_id=campaign.id,
            destination_id=destination_id,
            campaign_button_id=campaign_button_id,
            tracking_code=f"tg_{secrets.token_urlsafe(12)}",
            target_url=target_url,
            expires_at=expires_at,
        )
        self.db.add(item)
        self.db.flush()
        return item
