from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from tg_automation.storage.models import AuditLog


def audit(
    db: Session,
    *,
    actor_id: str | None,
    action: str,
    resource_type: str,
    resource_id: str | None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    reason: str | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            before_data=before,
            after_data=after,
            reason=reason,
        )
    )
