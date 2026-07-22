from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from tg_automation.storage.enums import RecordStatus


class MediaCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    file_path: str = Field(min_length=1, max_length=1000)
    telegram_file_id: str | None = Field(default=None, max_length=512)


class MediaRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    file_path: str
    telegram_file_id: str | None
    last_used_at: datetime | None
    usage_count: int
    status: RecordStatus
    created_at: datetime
