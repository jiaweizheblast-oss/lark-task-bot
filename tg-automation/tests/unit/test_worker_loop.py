from __future__ import annotations

import asyncio

import pytest

from apps.worker import main as worker_main
from tg_automation.core.config import clear_settings_cache


@pytest.mark.anyio
async def test_persistent_worker_backs_off_after_cycle_failure(monkeypatch) -> None:
    calls = 0
    delays: list[float] = []

    async def failing_cycle() -> int:
        nonlocal calls
        calls += 1
        raise RuntimeError("temporary database outage")

    async def stop_after_sleep(delay: float) -> None:
        delays.append(delay)
        raise asyncio.CancelledError

    monkeypatch.setenv("WORKER_ERROR_BACKOFF_SECONDS", "7")
    clear_settings_cache()
    monkeypatch.setattr(worker_main, "run_once", failing_cycle)
    monkeypatch.setattr(worker_main.asyncio, "sleep", stop_after_sleep)
    try:
        with pytest.raises(asyncio.CancelledError):
            await worker_main.run_forever()
    finally:
        clear_settings_cache()

    assert calls == 1
    assert delays == [7.0]
