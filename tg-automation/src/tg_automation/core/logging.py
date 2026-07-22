from __future__ import annotations

import logging
from typing import Any

SENSITIVE_KEYS = {"token", "bot_token", "authorization", "secret", "password"}


class SensitiveDataFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, dict):
            record.args = {
                key: "[REDACTED]" if key.lower() in SENSITIVE_KEYS else value
                for key, value in record.args.items()
            }
        return True


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(SensitiveDataFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    logging.getLogger("httpx").setLevel(logging.WARNING)


def redacted_config(settings: Any) -> dict[str, Any]:
    data = settings.model_dump()
    for key in tuple(data):
        if key.lower() in SENSITIVE_KEYS or key.lower().endswith(("_token", "_secret")):
            data[key] = "[REDACTED]" if data[key] else None
    return data
