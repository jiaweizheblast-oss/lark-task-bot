from __future__ import annotations

import html
import re
from datetime import datetime

from tg_automation.core.errors import DomainError
from tg_automation.storage.models import CampaignButton, ContentItem

VARIABLE_PATTERN = re.compile(r"{{\s*([a-zA-Z0-9_]+)\s*}}")


def format_time(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def render_caption(content: ContentItem) -> str:
    values = {
        "title": content.title,
        "valid_until": format_time(content.valid_until),
        "game_name": content.title,
    }

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            return match.group(0)
        return html.escape(values[key])

    rendered = VARIABLE_PATTERN.sub(replace, content.caption).strip()
    unresolved = sorted(set(VARIABLE_PATTERN.findall(rendered)))
    if unresolved:
        raise DomainError(
            "UNRESOLVED_TEMPLATE_VARIABLES",
            "Caption contains unsupported or unresolved template variables.",
            422,
            {"variables": unresolved},
        )
    if len(rendered) > 1024:
        raise DomainError(
            "CAPTION_TOO_LONG",
            "Rendered Telegram photo caption exceeds 1024 characters.",
            422,
        )
    return rendered


def render_buttons(buttons: list[CampaignButton]) -> list[dict]:
    rendered: list[dict] = []
    for item in sorted(buttons, key=lambda value: (value.row_number, value.position)):
        if not item.is_enabled:
            continue
        target = item.target_url
        if not target:
            raise DomainError(
                "BUTTON_TARGET_MISSING",
                f"Button {item.label} has no valid target.",
                422,
            )
        rendered.append(
            {
                "button_id": item.id,
                "type": item.button_type.value,
                "label": item.label,
                "value": target,
                "row": item.row_number,
                "position": item.position,
            }
        )
    return rendered
