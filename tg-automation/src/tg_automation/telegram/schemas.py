from __future__ import annotations

from pydantic import BaseModel, Field, HttpUrl, field_validator


class TelegramButton(BaseModel):
    label: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=2000)
    row: int = Field(default=0, ge=0, le=2)
    position: int = Field(default=0, ge=0, le=1)

    @field_validator("value")
    @classmethod
    def validate_url(cls, value: str) -> str:
        HttpUrl(value)
        return value


class TestSendRequest(BaseModel):
    photo: str = Field(min_length=1, max_length=2000)
    caption: str = Field(min_length=1, max_length=1024)
    buttons: list[TelegramButton] = Field(default_factory=list, max_length=5)


class TelegramSendResult(BaseModel):
    chat_id: str
    message_id: int


class TelegramPermissionResult(BaseModel):
    chat_id: str
    chat_title: str | None = None
    chat_type: str
    can_post: bool
