from __future__ import annotations

from tg_automation.telegram.gateway import build_keyboard
from tg_automation.telegram.schemas import TelegramButton


def test_keyboard_preserves_rows_for_url_buttons() -> None:
    markup = build_keyboard(
        [
            TelegramButton(
                label="OPEN EVENT",
                value="https://example.com/event",
                row=0,
                position=1,
            ),
            TelegramButton(
                label="MORE PROMOTIONS",
                value="https://example.com/promotions",
                row=0,
                position=0,
            ),
            TelegramButton(
                label="SUPPORT",
                value="https://t.me/example_support",
                row=1,
                position=0,
            ),
        ]
    )

    assert markup is not None
    assert len(markup.inline_keyboard) == 2
    assert markup.inline_keyboard[0][0].url == "https://example.com/promotions"
    assert markup.inline_keyboard[0][1].url == "https://example.com/event"
