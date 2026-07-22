from __future__ import annotations

from tg_automation.storage.enums import ButtonType, ContentType

FIRST_VERSION_HIDDEN_CONTENT_TYPES = {
    ContentType.WELCOME_BONUS,
    ContentType.VIP_BONUS,
    ContentType.DEPOSIT_BONUS,
    ContentType.EMERGENCY_NOTICE,
    ContentType.INDUSTRY_CONTENT,
}


def preset(
    preset_id: str,
    content_type: ContentType,
    label: str,
    caption: str,
    buttons: list[ButtonType],
    *,
    required_fields: list[str] | None = None,
    automation_policy: str = "REVIEW_REQUIRED",
) -> dict:
    return {
        "id": preset_id,
        "content_type": content_type.value,
        "label": label,
        "caption_template": caption,
        "recommended_buttons": [button.value for button in buttons],
        "required_fields": required_fields or ["title", "caption"],
        "automation_policy": automation_policy,
    }


CONTENT_PRESETS = [
    preset(
        "website-announcement",
        ContentType.WEBSITE_ANNOUNCEMENT,
        "Website Announcement",
        "📢 IMPORTANT ANNOUNCEMENT\n\n{{title}}\n\nAdd the announcement summary here.",
        [ButtonType.VIEW_DETAILS, ButtonType.CUSTOMER_SERVICE],
    ),
    preset(
        "new-game-launch",
        ContentType.NEW_GAME,
        "New Game / Hot Launch",
        "🔥 HOT LAUNCH!\n\n{{game_name}} is now online!\n\nExplore the latest game and rewards.",
        [ButtonType.PLAY_NOW],
    ),
    preset(
        "new-feature",
        ContentType.NEW_FEATURE,
        "New Feature",
        "✨ NEW FEATURE AVAILABLE\n\n{{title}}\n\nA new feature is now available. Try it today.",
        [ButtonType.VIEW_DETAILS],
    ),
    preset(
        "bank-delay",
        ContentType.BANK_DELAY,
        "Bank Delay Support",
        "🏦 BANK DELAY? 21GAME HAS YOUR BACK!\n\n{{title}}\n\n"
        "Check the latest support update below.",
        [ButtonType.VIEW_DETAILS, ButtonType.CUSTOMER_SERVICE],
    ),
    preset(
        "daily-event",
        ContentType.DAILY_EVENT,
        "Daily Event",
        "✨ DAILY EVENT\n\n{{title}}\n\nAvailable for a limited time.",
        [ButtonType.CLAIM_NOW],
        automation_policy="SCHEDULE_ALLOWED_AFTER_APPROVAL",
    ),
    preset(
        "lucky-spin",
        ContentType.LUCKY_SPIN,
        "Lucky Spin",
        "🎡 LUCKY SPIN — YOUR DAILY CASH BOOST\n\n"
        "Your daily spin is ready. Available for a limited time.",
        [ButtonType.SPIN_NOW],
        automation_policy="SCHEDULE_ALLOWED_AFTER_APPROVAL",
    ),
    preset(
        "deposit-bonus",
        ContentType.DEPOSIT_BONUS,
        "Deposit Bonus",
        "💰 DEPOSIT BONUS\n\n{{title}}\n\nCheck the current offer and eligibility before it ends.",
        [ButtonType.CLAIM_NOW],
    ),
    preset(
        "welcome-bonus",
        ContentType.WELCOME_BONUS,
        "Welcome Bonus",
        "🎁 WELCOME BONUS\n\n{{title}}\n\nYour welcome offer is ready. View the details below.",
        [ButtonType.CLAIM_NOW],
    ),
    preset(
        "vip-bonus",
        ContentType.VIP_BONUS,
        "VIP Bonus",
        "💎 VIP REWARDS\n\n{{title}}\n\nView the latest VIP offer and eligibility details.",
        [ButtonType.VIEW_DETAILS],
    ),
    preset(
        "industry-content",
        ContentType.INDUSTRY_CONTENT,
        "Industry Content",
        "📰 INDUSTRY UPDATE\n\n{{title}}\n\n"
        "Add a short reviewed summary and link to the approved source.",
        [ButtonType.VIEW_DETAILS],
        automation_policy="MANUAL_REVIEW_REQUIRED",
    ),
    preset(
        "emergency-notice",
        ContentType.EMERGENCY_NOTICE,
        "Emergency Notice",
        "⚠️ IMPORTANT SERVICE NOTICE\n\n{{title}}\n\nAdd the latest service information here.",
        [ButtonType.VIEW_DETAILS, ButtonType.CUSTOMER_SERVICE],
        automation_policy="MANUAL_APPROVAL_AND_SEND",
    ),
]


def list_presets(content_type: ContentType | None = None) -> list[dict]:
    if content_type is None:
        return [
            item
            for item in CONTENT_PRESETS
            if ContentType(item["content_type"]) not in FIRST_VERSION_HIDDEN_CONTENT_TYPES
        ]
    if content_type in FIRST_VERSION_HIDDEN_CONTENT_TYPES:
        return []
    return [item for item in CONTENT_PRESETS if item["content_type"] == content_type.value]


def get_preset(preset_id: str) -> dict | None:
    return next((item for item in CONTENT_PRESETS if item["id"] == preset_id), None)
