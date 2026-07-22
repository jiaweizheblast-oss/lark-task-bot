from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: Literal["development", "test", "staging", "production"] = "development"
    app_name: str = "NEXUS TG Automation Centre"
    database_url: str = "sqlite:///./tg_automation.db"
    log_level: str = "INFO"
    default_timezone: str = "Asia/Kolkata"
    global_sending_enabled: bool = False
    telegram_test_sending_enabled: bool = False
    embedded_worker_enabled: bool = False
    media_storage_dir: str = ""

    telegram_bot_token: SecretStr | None = None
    telegram_bot_username: str = "Game21HubBot"
    telegram_webhook_secret: SecretStr | None = None
    telegram_webhook_url: str | None = None
    telegram_admin_user_ids: str = ""
    telegram_test_channel_id: str = ""
    telegram_test_group_id: str = ""
    nexus_admin_url: str = "http://localhost:3000"

    tracking_base_url: str = "http://localhost:8000/r"
    support_url: str = "https://t.me/example_support"
    promotions_url: str = "https://example.com/promotions"
    tracking_allowed_hosts: str = "example.com,app.21.game,t.me"

    api_auth_enabled: bool = False
    nexus_operator_api_key: SecretStr | None = None
    nexus_admin_api_key: SecretStr | None = None

    worker_poll_seconds: float = Field(default=2.0, ge=0.2, le=60)
    worker_error_backoff_seconds: float = Field(default=10.0, ge=1, le=300)
    worker_batch_size: int = Field(default=20, ge=1, le=200)

    api_prefix: str = "/api/v1"
    worker_id: str = Field(default="worker-local", min_length=1, max_length=100)

    @field_validator(
        "telegram_bot_token",
        "telegram_webhook_secret",
        "nexus_operator_api_key",
        "nexus_admin_api_key",
        mode="before",
    )
    @classmethod
    def blank_secret_is_unset(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        if isinstance(value, SecretStr) and not value.get_secret_value().strip():
            return None
        return value

    @model_validator(mode="after")
    def production_safety(self) -> Settings:
        for raw_id in self.telegram_admin_user_ids.split(","):
            candidate = raw_id.strip()
            if candidate and (not candidate.isdigit() or int(candidate) <= 0):
                raise ValueError("TELEGRAM_ADMIN_USER_IDS must contain positive numeric user IDs.")
        if self.global_sending_enabled and self.telegram_bot_token is None:
            raise ValueError("Telegram sending cannot be enabled without a Bot Token.")
        if self.app_env in {"staging", "production"}:
            if not self.api_auth_enabled or self.nexus_admin_api_key is None:
                raise ValueError("Staging and production require NEXUS API authentication.")
            if len(self.nexus_admin_api_key.get_secret_value()) < 24:
                raise ValueError("The production NEXUS admin key must be at least 24 characters.")
            if not self.tracking_base_url.startswith("https://"):
                raise ValueError("Production tracking links must use HTTPS.")
            if self.telegram_bot_token is not None and not self.admin_user_ids:
                raise ValueError("The Telegram admin Bot requires at least one administrator.")
            if self.telegram_bot_token is not None and not self.nexus_admin_url.startswith(
                "https://"
            ):
                raise ValueError("The production NEXUS admin URL must use HTTPS.")
        configured_keys = [
            value.get_secret_value()
            for value in (
                self.nexus_operator_api_key,
                self.nexus_admin_api_key,
            )
            if value is not None
        ]
        if len(configured_keys) != len(set(configured_keys)):
            raise ValueError("NEXUS API keys must be unique across roles.")
        return self

    @property
    def allowed_redirect_hosts(self) -> set[str]:
        return {
            item.strip().lower().rstrip(".")
            for item in self.tracking_allowed_hosts.split(",")
            if item.strip()
        }

    @property
    def admin_user_ids(self) -> set[int]:
        values: set[int] = set()
        for item in self.telegram_admin_user_ids.split(","):
            candidate = item.strip()
            if candidate:
                values.add(int(candidate))
        return values

    @property
    def resolved_media_storage_dir(self) -> Path:
        if self.media_storage_dir.strip():
            return Path(self.media_storage_dir).expanduser().resolve()
        if self.database_url.startswith("sqlite:///"):
            database_path = Path(self.database_url.removeprefix("sqlite:///")).resolve()
            return database_path.parent / "media"
        return Path("./data/media").resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
