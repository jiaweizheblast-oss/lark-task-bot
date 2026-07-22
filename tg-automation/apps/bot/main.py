from __future__ import annotations

import logging
from collections.abc import Callable

from sqlalchemy.orm import Session
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from tg_automation.admin_bot.service import AdminBotService
from tg_automation.core.config import Settings, get_settings
from tg_automation.core.errors import ConfigurationError
from tg_automation.core.logging import configure_logging
from tg_automation.storage.database import get_session_factory

LOGGER = logging.getLogger(__name__)
SESSION_FACTORY_KEY = "session_factory"
SETTINGS_KEY = "settings"


def main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🕒 SCHEDULED", callback_data="admin:scheduled"),
                InlineKeyboardButton("👥 GROUPS", callback_data="admin:destinations"),
            ],
            [InlineKeyboardButton("📊 SENDING STATUS", callback_data="admin:status")],
        ]
    )


def is_authorized(update: Update, settings: Settings) -> bool:
    return bool(update.effective_user and update.effective_user.id in settings.admin_user_ids)


async def reject_unauthorized(update: Update) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(
            "This bot is restricted to authorised administrators."
        )


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data[SETTINGS_KEY]
    if not is_authorized(update, settings):
        await reject_unauthorized(update)
        return
    await update.effective_message.reply_text(
        "🛠 TG AUTOMATION\n\nMonitor group and channel publishing.",
        reply_markup=main_keyboard(),
    )


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data[SETTINGS_KEY]
    if not is_authorized(update, settings):
        await reject_unauthorized(update)
        return
    await update.effective_message.reply_text(
        "Use this Bot to check destinations, schedules and sending status.",
        reply_markup=main_keyboard(),
    )


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    settings: Settings = context.application.bot_data[SETTINGS_KEY]
    if not query:
        return
    if not is_authorized(update, settings):
        await query.answer("Administrators only.", show_alert=True)
        return
    await query.answer()
    factory: Callable[[], Session] = context.application.bot_data[SESSION_FACTORY_KEY]
    with factory() as db:
        service = AdminBotService(db)
        if query.data == "admin:scheduled":
            message = service.scheduled_text()
        elif query.data == "admin:destinations":
            message = service.destinations_text()
        elif query.data == "admin:status":
            message = service.status_text()
        else:
            message = "TG Automation"
    await query.edit_message_text(message, reply_markup=main_keyboard())


async def error_handler(_update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    LOGGER.error("Unhandled admin Bot update error: %s", context.error)


def build_application(
    settings: Settings,
    session_factory: Callable[[], Session],
) -> Application:
    if settings.telegram_bot_token is None:
        raise ConfigurationError("TELEGRAM_BOT_TOKEN is not configured.")
    if not settings.admin_user_ids:
        raise ConfigurationError("TELEGRAM_ADMIN_USER_IDS is not configured.")
    application = (
        Application.builder().token(settings.telegram_bot_token.get_secret_value()).build()
    )
    application.bot_data[SESSION_FACTORY_KEY] = session_factory
    application.bot_data[SETTINGS_KEY] = settings
    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("help", help_handler))
    application.add_handler(CallbackQueryHandler(callback_handler, pattern=r"^admin:"))
    application.add_error_handler(error_handler)
    return application


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    build_application(settings, get_session_factory()).run_polling(
        allowed_updates=["message", "callback_query"]
    )


if __name__ == "__main__":
    main()
