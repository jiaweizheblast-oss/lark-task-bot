from __future__ import annotations

import asyncio
import logging

from tg_automation.core.config import get_settings
from tg_automation.core.logging import configure_logging
from tg_automation.deliveries.service import DeliveryService
from tg_automation.storage.database import get_session_factory
from tg_automation.telegram.gateway import OfficialTelegramGateway

LOGGER = logging.getLogger(__name__)


async def run_once() -> int:
    settings = get_settings()
    if not settings.global_sending_enabled:
        LOGGER.warning("Global sending is disabled; worker did not claim tasks.")
        return 0

    gateway = OfficialTelegramGateway(settings)
    with get_session_factory()() as db:
        service = DeliveryService(db, settings.worker_id, settings)
        ids = service.claim_ready(limit=settings.worker_batch_size)
        for delivery_id in ids:
            try:
                await service.process(delivery_id, gateway)
            except Exception:
                db.rollback()
                LOGGER.exception("Public delivery %s failed unexpectedly.", delivery_id)
        return len(ids)


async def run_forever() -> None:
    settings = get_settings()
    while True:
        try:
            await run_once()
            delay = settings.worker_poll_seconds
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("Worker cycle failed; backing off before retry.")
            delay = settings.worker_error_backoff_seconds
        await asyncio.sleep(delay)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    processed = asyncio.run(run_once())
    LOGGER.info("Worker processed %s delivery tasks.", processed)


def main_forever() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        asyncio.run(run_forever())
    except KeyboardInterrupt:
        LOGGER.info("Worker stopped by operator.")


if __name__ == "__main__":
    main()
