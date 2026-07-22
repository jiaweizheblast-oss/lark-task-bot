from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from tg_automation.core.api import success
from tg_automation.core.auth import Actor, require_approver
from tg_automation.storage.database import get_db
from tg_automation.storage.models import AuditLog

router = APIRouter(prefix="/tg/audit-logs", tags=["audit"])


@router.get("")
def list_audit_logs(
    resource_type: str | None = Query(default=None, max_length=100),
    actor_id: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=100, ge=1, le=500),
    _actor: Actor = Depends(require_approver),
    db: Session = Depends(get_db),
) -> dict:
    query = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    if resource_type:
        query = query.where(AuditLog.resource_type == resource_type)
    if actor_id:
        query = query.where(AuditLog.actor_id == actor_id)
    items = db.scalars(query).all()
    return success(
        [
            {
                "id": item.id,
                "actor_id": item.actor_id,
                "action": item.action,
                "resource_type": item.resource_type,
                "resource_id": item.resource_id,
                "before": item.before_data,
                "after": item.after_data,
                "reason": item.reason,
                "created_at": item.created_at.isoformat(),
            }
            for item in items
        ]
    )
