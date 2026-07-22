from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from tg_automation.storage.enums import (
    ButtonType,
    CampaignStatus,
    PublishMode,
)


class CampaignButtonCreate(BaseModel):
    button_type: ButtonType
    label: str = Field(min_length=1, max_length=64)
    target_url: str | None = Field(default=None, max_length=2000)
    row_number: int = Field(ge=0, le=2)
    position: int = Field(ge=0, le=1)

    @field_validator("label")
    @classmethod
    def clean_label(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Button label cannot be blank.")
        return cleaned

    @model_validator(mode="after")
    def validate_target(self) -> CampaignButtonCreate:
        if not self.target_url:
            raise ValueError("Non-copy buttons require target_url.")
        HttpUrl(self.target_url)
        return self


class CampaignCreate(BaseModel):
    content_id: str
    media_id: str | None = None
    destination_ids: list[str] = Field(min_length=1)
    publish_mode: PublishMode = PublishMode.SCHEDULED
    scheduled_at: datetime | None = None
    display_timezone: str = Field(default="Asia/Kolkata", min_length=1, max_length=64)
    buttons: list[CampaignButtonCreate] = Field(default_factory=list, max_length=5)
    created_by: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def validate_schedule_and_buttons(self) -> CampaignCreate:
        if self.publish_mode == PublishMode.SCHEDULED and self.scheduled_at is None:
            raise ValueError("scheduled_at is required for scheduled campaigns.")
        positions = {(item.row_number, item.position) for item in self.buttons}
        if len(positions) != len(self.buttons):
            raise ValueError("Campaign buttons cannot share the same position.")
        labels = {item.label.casefold() for item in self.buttons}
        if len(labels) != len(self.buttons):
            raise ValueError("Campaign button labels must be unique.")
        return self


class CampaignUpdate(BaseModel):
    content_id: str | None = None
    media_id: str | None = None
    destination_ids: list[str] | None = Field(default=None, min_length=1)
    publish_mode: PublishMode | None = None
    scheduled_at: datetime | None = None
    display_timezone: str | None = Field(default=None, min_length=1, max_length=64)
    buttons: list[CampaignButtonCreate] | None = Field(default=None, max_length=5)

    @model_validator(mode="after")
    def validate_button_positions(self) -> CampaignUpdate:
        if self.buttons is not None:
            positions = {(item.row_number, item.position) for item in self.buttons}
            if len(positions) != len(self.buttons):
                raise ValueError("Campaign buttons cannot share the same position.")
            labels = {item.label.casefold() for item in self.buttons}
            if len(labels) != len(self.buttons):
                raise ValueError("Campaign button labels must be unique.")
        return self


class CampaignRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    campaign_code: str
    content_id: str
    media_id: str | None
    publish_mode: PublishMode
    scheduled_at: datetime | None
    display_timezone: str
    status: CampaignStatus
    created_by: str | None
    approved_by: str | None
    approved_at: datetime | None
    sent_at: datetime | None
    rendered_caption: str | None
    rendered_buttons: list[dict] | None
    rendered_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ApproveRequest(BaseModel):
    # Kept optional for backward-compatible clients; authenticated actor identity wins.
    approved_by: str | None = Field(default=None, min_length=1, max_length=100)


class ScheduleRequest(BaseModel):
    scheduled_at: datetime


class PreflightRequest(BaseModel):
    scheduled_at: datetime | None = None


class TestPreviewRequest(BaseModel):
    destination_id: str


class CampaignPreview(BaseModel):
    campaign_id: str
    campaign_code: str
    caption: str
    media_id: str
    photo: str
    buttons: list[dict]
    destination_count: int
