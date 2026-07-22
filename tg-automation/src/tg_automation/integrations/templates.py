from __future__ import annotations

import html

from tg_automation.core.errors import DomainError
from tg_automation.integrations.schemas import NexusContentEvent
from tg_automation.storage.enums import ContentType

HEADINGS = {
    ContentType.WEBSITE_ANNOUNCEMENT: "📢 IMPORTANT ANNOUNCEMENT",
    ContentType.NEW_GAME: "🔥 HOT LAUNCH!",
    ContentType.NEW_FEATURE: "✨ NEW FEATURE AVAILABLE",
    ContentType.BANK_DELAY: "🏦 BANK DELAY? 21GAME HAS YOUR BACK!",
    ContentType.DAILY_EVENT: "✨ DAILY EVENT",
    ContentType.LUCKY_SPIN: "🎡 LUCKY SPIN — YOUR DAILY CASH BOOST",
}


def render_nexus_caption(event: NexusContentEvent) -> str:
    heading = HEADINGS[event.content_type]
    title = html.escape(event.title.strip())
    summary = html.escape(event.summary.strip())
    if event.content_type == ContentType.NEW_GAME:
        body = f"{heading}\n\n{title} is now online!\n\n{summary}\n\nTap below to play now."
    elif event.content_type == ContentType.NEW_FEATURE:
        body = f"{heading}\n\n{title}\n\n{summary}\n\nTry it now."
    elif event.content_type == ContentType.LUCKY_SPIN:
        body = f"{heading}\n\n{summary}\n\nAvailable for a limited time."
    else:
        body = f"{heading}\n\n{title}\n\n{summary}"
    if len(body) > 1024:
        raise DomainError(
            "SYNCED_CAPTION_TOO_LONG",
            "The generated Telegram caption exceeds 1024 characters.",
            422,
        )
    return body
