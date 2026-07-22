from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from tg_automation.analytics.service import AnalyticsService
from tg_automation.core.api import success
from tg_automation.storage.database import get_db

router = APIRouter(prefix="/tg/analytics", tags=["analytics"])


@router.get("/overview")
def overview(include_test: bool = Query(default=False), db: Session = Depends(get_db)) -> dict:
    return success(AnalyticsService(db, include_test).overview())


@router.get("/campaigns")
def campaigns(include_test: bool = Query(default=False), db: Session = Depends(get_db)) -> dict:
    return success(AnalyticsService(db, include_test).campaigns())


@router.get("/media")
def media(include_test: bool = Query(default=False), db: Session = Depends(get_db)) -> dict:
    return success(AnalyticsService(db, include_test).media())


@router.get("/destinations")
def destinations(include_test: bool = Query(default=False), db: Session = Depends(get_db)) -> dict:
    return success(AnalyticsService(db, include_test).destinations())
