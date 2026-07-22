from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Protocol

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatMemberStatus, ChatType
from telegram.error import BadRequest, Forbidden, InvalidToken, NetworkError, RetryAfter

from tg_automation.core.config import Settings
from tg_automation.core.errors import ConfigurationError, DomainError
from tg_automation.telegram.schemas import (
    TelegramButton,
    TelegramPermissionResult,
    TelegramSendResult,
)


class TelegramGateway(Protocol):
    async def send_photo(
        self,
        chat_id: str,
        photo: str,
        caption: str,
        buttons: list[TelegramButton],
    ) -> TelegramSendResult: ...

    async def check_permissions(self, chat_id: str) -> TelegramPermissionResult: ...


def build_keyboard(buttons: list[TelegramButton]) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    rows: dict[int, list[TelegramButton]] = defaultdict(list)
    for button in sorted(buttons, key=lambda item: (item.row, item.position)):
        rows[button.row].append(button)

    keyboard: list[list[InlineKeyboardButton]] = []
    for row_number in sorted(rows):
        telegram_row: list[InlineKeyboardButton] = []
        for item in rows[row_number]:
            telegram_row.append(InlineKeyboardButton(text=item.label, url=item.value))
        keyboard.append(telegram_row)
    return InlineKeyboardMarkup(keyboard)


class OfficialTelegramGateway:
    def __init__(self, settings: Settings) -> None:
        if settings.telegram_bot_token is None:
            raise ConfigurationError("TELEGRAM_BOT_TOKEN is not configured.")
        self._token = settings.telegram_bot_token.get_secret_value()

    async def send_photo(
        self,
        chat_id: str,
        photo: str,
        caption: str,
        buttons: list[TelegramButton],
    ) -> TelegramSendResult:
        image: str | object = photo
        stream = None
        path = Path(photo)
        if not photo.startswith(("http://", "https://")) and path.is_file():
            stream = path.open("rb")
            image = stream
        try:
            async with Bot(self._token) as bot:
                message = await bot.send_photo(
                    chat_id=chat_id,
                    photo=image,
                    caption=caption,
                    reply_markup=build_keyboard(buttons),
                )
            return TelegramSendResult(chat_id=str(message.chat_id), message_id=message.message_id)
        except RetryAfter as exc:
            retry_after = exc.retry_after
            retry_seconds = (
                int(retry_after.total_seconds())
                if hasattr(retry_after, "total_seconds")
                else int(retry_after)
            )
            raise DomainError(
                "TELEGRAM_RATE_LIMITED",
                "Telegram rate limit was reached.",
                503,
                {"retry_after_seconds": retry_seconds},
            ) from exc
        except Forbidden as exc:
            raise DomainError(
                "TELEGRAM_FORBIDDEN",
                "The bot is not allowed to post to this destination.",
                422,
            ) from exc
        except InvalidToken as exc:
            raise ConfigurationError("Telegram Bot Token is invalid.") from exc
        except BadRequest as exc:
            raise DomainError("TELEGRAM_BAD_REQUEST", str(exc), 422) from exc
        except NetworkError as exc:
            raise DomainError(
                "TELEGRAM_NETWORK_ERROR",
                "Telegram is temporarily unreachable.",
                503,
            ) from exc
        finally:
            if stream is not None:
                stream.close()

    async def check_permissions(self, chat_id: str) -> TelegramPermissionResult:
        try:
            async with Bot(self._token) as bot:
                bot_user = await bot.get_me()
                chat = await bot.get_chat(chat_id)
                member = await bot.get_chat_member(chat_id, bot_user.id)
            is_owner = member.status == ChatMemberStatus.OWNER
            is_admin = member.status == ChatMemberStatus.ADMINISTRATOR
            if chat.type == ChatType.CHANNEL:
                can_post = is_owner or (
                    is_admin and bool(getattr(member, "can_post_messages", False))
                )
            elif member.status == ChatMemberStatus.RESTRICTED:
                can_post = bool(getattr(member, "can_send_messages", False))
            else:
                can_post = member.status in {
                    ChatMemberStatus.OWNER,
                    ChatMemberStatus.ADMINISTRATOR,
                    ChatMemberStatus.MEMBER,
                }
            return TelegramPermissionResult(
                chat_id=str(chat.id),
                chat_title=getattr(chat, "title", None),
                chat_type=str(chat.type),
                can_post=can_post,
            )
        except Forbidden as exc:
            raise DomainError(
                "TELEGRAM_FORBIDDEN",
                "The bot cannot inspect this destination.",
                422,
            ) from exc
        except InvalidToken as exc:
            raise ConfigurationError("Telegram Bot Token is invalid.") from exc
        except BadRequest as exc:
            raise DomainError("TELEGRAM_BAD_REQUEST", str(exc), 422) from exc
        except NetworkError as exc:
            raise DomainError(
                "TELEGRAM_NETWORK_ERROR",
                "Telegram is temporarily unreachable.",
                503,
            ) from exc
