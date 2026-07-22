from __future__ import annotations

from datetime import timedelta

from tg_automation.core.time import utc_now
from tg_automation.media.schemas import MediaCreate
from tg_automation.media.service import MediaService
from tg_automation.storage.enums import RecordStatus


def test_recommend_ignores_disabled_images(session) -> None:
    service = MediaService(session)
    disabled = service.create(MediaCreate(name="Disabled", file_path="assets/disabled.jpg"))
    service.set_status(disabled.id, RecordStatus.DISABLED)
    enabled = service.create(MediaCreate(name="Enabled", file_path="assets/enabled.jpg"))

    assert service.recommend().id == enabled.id


def test_recommend_prefers_never_used_then_least_recently_used(session) -> None:
    service = MediaService(session)
    recent = service.create(MediaCreate(name="Recent", file_path="assets/recent.jpg"))
    recent.last_used_at = utc_now()
    old = service.create(MediaCreate(name="Old", file_path="assets/old.jpg"))
    old.last_used_at = utc_now() - timedelta(days=8)
    fresh = service.create(MediaCreate(name="Never used", file_path="assets/fresh.jpg"))
    session.commit()

    assert service.recommend().id == fresh.id

    fresh.last_used_at = utc_now() - timedelta(days=1)
    session.commit()
    assert service.recommend().id == old.id
