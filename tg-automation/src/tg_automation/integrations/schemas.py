from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl, model_validator

from tg_automation.storage.enums import ContentStatus, ContentType

SYNC_CONTENT_TYPES = {
    ContentType.WEBSITE_ANNOUNCEMENT,
    ContentType.NEW_GAME,
    ContentType.NEW_FEATURE,
    ContentType.BANK_DELAY,
    ContentType.DAILY_EVENT,
    ContentType.LUCKY_SPIN,
}


class NexusContentEvent(BaseModel):
    external_event_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")
    content_type: ContentType
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=600)
    action_url: HttpUrl | None = None
    language: str = Field(default="en", min_length=2, max_length=16)
    published_at: datetime | None = None
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @model_validator(mode="after")
    def supported_content_type(self) -> NexusContentEvent:
        if self.content_type not in SYNC_CONTENT_TYPES:
            raise ValueError("This content type cannot be imported from website events.")
        return self


class NexusContentEventResult(BaseModel):
    integration_event_id: str
    content_id: str | None
    duplicate: bool
    status: str


class NexusContentEventRead(NexusContentEventResult):
    external_event_id: str
    event_type: str
    payload: dict
    received_at: datetime
    processed_at: datetime | None
    content_status: ContentStatus | None = None
    content_title: str | None = None
    telegram_caption: str | None = None


class NexusCampaignDraftRequest(BaseModel):
    destination_ids: list[str] = Field(min_length=1, max_length=100)
    scheduled_at: datetime
    media_id: str | None = None

    @model_validator(mode="after")
    def unique_destinations(self) -> NexusCampaignDraftRequest:
        if len(self.destination_ids) != len(set(self.destination_ids)):
            raise ValueError("destination_ids cannot contain duplicates.")
        return self


class NexusCampaignDraftResult(BaseModel):
    campaign_id: str
    campaign_code: str
    status: str
    duplicate: bool
    content_id: str
    button_type: str
