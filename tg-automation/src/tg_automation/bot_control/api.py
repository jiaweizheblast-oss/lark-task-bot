from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from tg_automation.bot_control.service import BotControlService
from tg_automation.core.api import success
from tg_automation.core.config import get_settings
from tg_automation.storage.database import get_db

router = APIRouter(prefix="/tg/bot-control", tags=["bot-control"])


@router.get("/overview")
def overview(
    include_test: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> dict:
    return success(BotControlService(db, get_settings()).overview(include_test))
