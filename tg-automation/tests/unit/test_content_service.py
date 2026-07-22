from __future__ import annotations

from datetime import timedelta

import pytest

from tg_automation.content.schemas import ContentCreate, ContentUpdate
from tg_automation.content.service import ContentService
from tg_automation.core.errors import DomainError
from tg_automation.core.time import utc_now
from tg_automation.storage.enums import ContentStatus, ContentType


def test_content_can_be_created_and_approved(session) -> None:
    content = ContentService(session).create(
        ContentCreate(
            content_type=ContentType.NEW_GAME,
            title="New game",
            caption="New game is online",
            valid_until=utc_now() + timedelta(hours=2),
        )
    )

    approved = ContentService(session).approve(content.id)

    assert approved.status == ContentStatus.APPROVED


def test_draft_content_can_be_edited_and_revalidated(session) -> None:
    service = ContentService(session)
    content = service.create(
        ContentCreate(
            content_type=ContentType.NEW_GAME,
            title="Old title",
            caption="Old caption",
        )
    )

    updated = service.update(
        content.id,
        ContentUpdate(title="Fortune Garuda 500", caption="🔥 HOT LAUNCH!"),
        "operator-1",
    )

    assert updated.title == "Fortune Garuda 500"
    assert updated.caption == "🔥 HOT LAUNCH!"


def test_approved_content_cannot_be_edited(session) -> None:
    service = ContentService(session)
    content = service.create(
        ContentCreate(
            content_type=ContentType.NEW_FEATURE,
            title="Feature",
            caption="New feature",
        )
    )
    service.approve(content.id)

    with pytest.raises(DomainError) as exc_info:
        service.update(content.id, ContentUpdate(title="Changed"), "operator-1")

    assert exc_info.value.code == "CONTENT_NOT_EDITABLE"
