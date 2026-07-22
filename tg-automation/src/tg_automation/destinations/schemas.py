from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tg_automation.storage.enums import DestinationType, RecordStatus


class DestinationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    telegram_chat_id: str = Field(min_length=2, max_length=100)
    destination_type: DestinationType
    source_code: str = Field(pattern=r"^[a-z0-9_-]{2,64}$")
    is_test: bool = False

    @model_validator(mode="after")
    def validate_test_type(self) -> DestinationCreate:
        type_is_test = self.destination_type in {
            DestinationType.TEST_CHANNEL,
            DestinationType.TEST_GROUP,
        }
        if self.is_test != type_is_test:
            raise ValueError("is_test must match the selected test destination type.")
        return self


class DestinationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    telegram_chat_id: str | None = Field(default=None, min_length=2, max_length=100)
    destination_type: DestinationType | None = None
    source_code: str | None = Field(default=None, pattern=r"^[a-z0-9_-]{2,64}$")
    is_test: bool | None = None


class DestinationBulkStatusRequest(BaseModel):
    destination_ids: list[str] = Field(min_length=1, max_length=100)
    status: RecordStatus

    @model_validator(mode="after")
    def reject_duplicates(self) -> DestinationBulkStatusRequest:
        if len(self.destination_ids) != len(set(self.destination_ids)):
            raise ValueError("destination_ids cannot contain duplicates.")
        return self


class DestinationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    telegram_chat_id: str
    destination_type: DestinationType
    source_code: str
    is_test: bool
    bot_can_post: bool
    last_permission_check: datetime | None
    status: RecordStatus
    created_at: datetime
