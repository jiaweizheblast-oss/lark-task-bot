from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tg_automation.storage.enums import ContentStatus, ContentType


class ContentCreate(BaseModel):
    content_type: ContentType
    title: str = Field(min_length=1, max_length=200)
    caption: str = Field(min_length=1, max_length=4000)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    source_type: str = Field(default="MANUAL", max_length=40)
    source_reference: str | None = Field(default=None, max_length=200)
    language: str = Field(default="en", min_length=2, max_length=16)
    created_by: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def validate_business_fields(self) -> ContentCreate:
        if self.valid_from and self.valid_until and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be later than valid_from.")
        return self


class ContentUpdate(BaseModel):
    content_type: ContentType | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    caption: str | None = Field(default=None, min_length=1, max_length=4000)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    source_type: str | None = Field(default=None, max_length=40)
    source_reference: str | None = Field(default=None, max_length=200)
    language: str | None = Field(default=None, min_length=2, max_length=16)


class ContentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    content_type: ContentType
    title: str
    caption: str
    valid_from: datetime | None
    valid_until: datetime | None
    source_type: str
    source_reference: str | None
    language: str
    status: ContentStatus
    created_by: str | None
    created_at: datetime
    updated_at: datetime
