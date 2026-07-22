from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

import uvicorn
from fastapi import Depends, FastAPI
from sqlalchemy import text

from apps.worker.main import run_forever
from tg_automation.analytics.api import router as analytics_router
from tg_automation.bot_control.api import router as bot_control_router
from tg_automation.campaigns.api import router as campaign_router
from tg_automation.content.api import router as content_router
from tg_automation.content_templates.api import router as content_template_router
from tg_automation.core.api import install_error_handlers, success
from tg_automation.core.audit_api import router as audit_router
from tg_automation.core.auth import require_viewer
from tg_automation.core.config import get_settings
from tg_automation.core.logging import configure_logging
from tg_automation.dashboard.api import router as dashboard_router
from tg_automation.destinations.api import router as destination_router
from tg_automation.destinations.bootstrap import bootstrap_test_destinations
from tg_automation.integrations.api import router as integration_router
from tg_automation.media.api import router as media_router
from tg_automation.operations.api import router as operations_router
from tg_automation.storage.database import get_engine
from tg_automation.test_schedules.api import router as test_schedule_router
from tg_automation.tracking.api import router as tracking_router

settings = get_settings()
configure_logging(settings.log_level)
LOGGER = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    bootstrap_test_destinations(settings)
    worker_task = None
    if settings.embedded_worker_enabled:
        worker_task = asyncio.create_task(run_forever(), name="tg-delivery-worker")
        LOGGER.info("Embedded Telegram delivery worker started.")
    try:
        yield
    finally:
        if worker_task is not None:
            worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await worker_task
            LOGGER.info("Embedded Telegram delivery worker stopped.")


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/docs" if settings.app_env != "production" else None,
    redoc_url=None,
    lifespan=lifespan,
)
install_error_handlers(app)
protected = [Depends(require_viewer)]
app.include_router(analytics_router, prefix=settings.api_prefix, dependencies=protected)
app.include_router(audit_router, prefix=settings.api_prefix, dependencies=protected)
app.include_router(bot_control_router, prefix=settings.api_prefix, dependencies=protected)
app.include_router(campaign_router, prefix=settings.api_prefix, dependencies=protected)
app.include_router(content_router, prefix=settings.api_prefix, dependencies=protected)
app.include_router(content_template_router, prefix=settings.api_prefix, dependencies=protected)
app.include_router(media_router, prefix=settings.api_prefix, dependencies=protected)
app.include_router(destination_router, prefix=settings.api_prefix, dependencies=protected)
app.include_router(dashboard_router, prefix=settings.api_prefix, dependencies=protected)
app.include_router(integration_router, prefix=settings.api_prefix, dependencies=protected)
app.include_router(operations_router, prefix=settings.api_prefix, dependencies=protected)
app.include_router(tracking_router)
app.include_router(test_schedule_router, prefix=settings.api_prefix, dependencies=protected)


@app.get("/health", tags=["system"])
def health() -> dict:
    return success(
        {
            "status": "ok",
            "environment": settings.app_env,
            "sending_enabled": settings.global_sending_enabled,
            "test_sending_enabled": settings.telegram_test_sending_enabled,
            "worker_enabled": settings.embedded_worker_enabled,
        }
    )


@app.get("/health/ready", tags=["system"])
def readiness() -> dict:
    with get_engine().connect() as connection:
        connection.execute(text("SELECT 1"))
    return success({"status": "ready", "database": "ok"})


def run() -> None:
    uvicorn.run("apps.api.main:app", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    run()
