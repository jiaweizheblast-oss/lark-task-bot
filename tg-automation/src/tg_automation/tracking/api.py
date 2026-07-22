from __future__ import annotations

import secrets

from fastapi import APIRouter, Cookie, Depends, Header
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from tg_automation.core.config import get_settings
from tg_automation.storage.database import get_db
from tg_automation.tracking.service import TrackingService

router = APIRouter(tags=["tracking"])


@router.get("/r/{tracking_code}", include_in_schema=False)
def redirect_tracking_link(
    tracking_code: str,
    db: Session = Depends(get_db),
    tg_vid: str | None = Cookie(default=None),
    user_agent: str | None = Header(default=None),
) -> RedirectResponse:
    settings = get_settings()
    service = TrackingService(db, settings)
    link = service.resolve(tracking_code)
    visitor_id = tg_vid if tg_vid and len(tg_vid) <= 100 else secrets.token_urlsafe(18)
    service.record_click(link, visitor_id, user_agent)

    response = RedirectResponse(link.target_url, status_code=302)
    if not tg_vid:
        response.set_cookie(
            "tg_vid",
            visitor_id,
            max_age=60 * 60 * 24 * 180,
            httponly=True,
            secure=settings.app_env in {"staging", "production"},
            samesite="lax",
        )
    return response
