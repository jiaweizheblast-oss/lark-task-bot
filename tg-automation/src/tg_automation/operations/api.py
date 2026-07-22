from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from tg_automation.core.api import success
from tg_automation.core.auth import Actor, require_operator
from tg_automation.operations.service import OperationsService
from tg_automation.storage.database import get_db

router = APIRouter(prefix="/tg/operations", tags=["operations"])


@router.get("/queue")
def queue_overview(
    include_test: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> dict:
    return success(OperationsService(db).queue_overview(include_test))


@router.get("/campaigns/{campaign_id}/deliveries")
def campaign_deliveries(campaign_id: str, db: Session = Depends(get_db)) -> dict:
    return success(OperationsService(db).campaign_deliveries(campaign_id))


@router.post("/deliveries/{delivery_id}/retry")
def retry_delivery(
    delivery_id: str,
    actor: Actor = Depends(require_operator),
    db: Session = Depends(get_db),
) -> dict:
    item = OperationsService(db).retry_public_delivery(delivery_id, actor.actor_id)
    return success(
        {
            "delivery_id": item.id,
            "status": item.status.value,
            "next_attempt_at": item.next_attempt_at.isoformat(),
        }
    )
