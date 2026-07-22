from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class DomainError(Exception):
    code: str
    message: str
    status_code: int = 400
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message


class NotFoundError(DomainError):
    def __init__(self, resource: str, resource_id: str) -> None:
        super().__init__(
            code=f"{resource.upper()}_NOT_FOUND",
            message=f"{resource} was not found.",
            status_code=404,
            details={"id": resource_id},
        )


class ConfigurationError(DomainError):
    def __init__(self, message: str) -> None:
        super().__init__("CONFIGURATION_ERROR", message, 503)
