from __future__ import annotations

import secrets
from dataclasses import dataclass
from enum import IntEnum

from fastapi import Header

from tg_automation.core.config import get_settings
from tg_automation.core.errors import ConfigurationError, DomainError


class NexusRole(IntEnum):
    OPERATOR = 20
    ADMIN = 40


@dataclass(frozen=True, slots=True)
class Actor:
    actor_id: str
    role: NexusRole


def _configured_keys() -> list[tuple[str, NexusRole]]:
    settings = get_settings()
    values = [
        (settings.nexus_operator_api_key, NexusRole.OPERATOR),
        (settings.nexus_admin_api_key, NexusRole.ADMIN),
    ]
    return [(value.get_secret_value(), role) for value, role in values if value is not None]


def require_role(minimum: NexusRole):
    def dependency(
        x_nexus_api_key: str | None = Header(default=None),
        x_nexus_actor: str | None = Header(default=None),
    ) -> Actor:
        settings = get_settings()
        if not settings.api_auth_enabled:
            return Actor(
                actor_id=(x_nexus_actor or "local-development")[:100], role=NexusRole.ADMIN
            )
        keys = _configured_keys()
        if not keys:
            raise ConfigurationError("NEXUS API authentication is enabled but no keys exist.")
        matched_role = next(
            (
                role
                for configured_key, role in keys
                if x_nexus_api_key and secrets.compare_digest(x_nexus_api_key, configured_key)
            ),
            None,
        )
        if matched_role is None:
            raise DomainError("AUTHENTICATION_REQUIRED", "A valid NEXUS API key is required.", 401)
        if matched_role < minimum:
            raise DomainError("INSUFFICIENT_ROLE", "This action requires a higher role.", 403)
        actor_id = (x_nexus_actor or f"nexus-{matched_role.name.lower()}").strip()[:100]
        return Actor(actor_id=actor_id or "unknown", role=matched_role)

    return dependency


require_operator = require_role(NexusRole.OPERATOR)
require_admin = require_role(NexusRole.ADMIN)

# Compatibility names keep route modules small while exposing only two actual roles.
require_viewer = require_operator
require_approver = require_admin
