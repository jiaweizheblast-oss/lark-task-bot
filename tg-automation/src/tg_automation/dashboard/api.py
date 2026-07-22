from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from tg_automation.core.api import success
from tg_automation.core.config import Settings, get_settings
from tg_automation.dashboard.service import DashboardService
from tg_automation.storage.database import get_db

router = APIRouter(prefix="/tg/dashboard", tags=["dashboard"])


@router.get("")
def dashboard(
    include_test: bool = Query(default=False),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    return success(DashboardService(db, settings, include_test).overview())
