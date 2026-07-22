from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import ValidationError

from tg_automation.core.errors import DomainError
from tg_automation.destinations.schemas import DestinationCreate, DestinationUpdate
from tg_automation.destinations.service import DestinationService
from tg_automation.storage.enums import DestinationType, RecordStatus
from tg_automation.telegram.schemas import (
    TelegramPermissionResult,
    TelegramSendResult,
)
from tg_automation.telegram.schemas import (
    TestSendRequest as PreviewSendRequest,
)


@dataclass
class FakeGateway:
    send_calls: int = 0

    async def check_permissions(self, chat_id: str) -> TelegramPermissionResult:
        return TelegramPermissionResult(
            chat_id=chat_id,
            chat_title="Automation Test",
            chat_type="channel",
            can_post=True,
        )

    async def send_photo(self, chat_id, photo, caption, buttons) -> TelegramSendResult:
        self.send_calls += 1
        return TelegramSendResult(chat_id=chat_id, message_id=9001)


def create_test_destination(session):
    return DestinationService(session).create(
        DestinationCreate(
            name="Test Channel",
            telegram_chat_id="-100123",
            destination_type=DestinationType.TEST_CHANNEL,
            source_code="test_channel",
            is_test=True,
        )
    )


def test_test_flag_must_match_destination_type() -> None:
    with pytest.raises(ValidationError, match="is_test must match"):
        DestinationCreate(
            name="Unsafe mismatch",
            telegram_chat_id="-100123",
            destination_type=DestinationType.CHANNEL,
            source_code="unsafe",
            is_test=True,
        )


async def test_permission_check_records_capabilities(session) -> None:
    destination = create_test_destination(session)

    checked = await DestinationService(session).check_permissions(
        destination.id, FakeGateway(), "operator-1"
    )

    assert checked.bot_can_post is True
    assert checked.last_permission_check is not None


async def test_test_send_requires_separate_safety_switch(session) -> None:
    destination = create_test_destination(session)
    gateway = FakeGateway()

    with pytest.raises(DomainError) as exc_info:
        await DestinationService(session).send_test(
            destination.id,
            PreviewSendRequest(photo="assets/test.jpg", caption="Preview"),
            gateway,
            test_sending_enabled=False,
        )

    assert exc_info.value.code == "TEST_SENDING_DISABLED"
    assert gateway.send_calls == 0


async def test_enabled_test_send_records_success(session) -> None:
    destination = create_test_destination(session)
    gateway = FakeGateway()

    result = await DestinationService(session).send_test(
        destination.id,
        PreviewSendRequest(photo="assets/test.jpg", caption="Preview"),
        gateway,
        test_sending_enabled=True,
        actor_id="operator-1",
    )

    assert result.message_id == 9001
    assert gateway.send_calls == 1
    assert destination.bot_can_post is True


def test_changing_chat_connection_clears_old_permission_result(session) -> None:
    destination = create_test_destination(session)
    destination.bot_can_post = True
    session.commit()

    updated = DestinationService(session).update(
        destination.id,
        DestinationUpdate(telegram_chat_id="-100456"),
        "operator-1",
    )

    assert updated.telegram_chat_id == "-100456"
    assert updated.bot_can_post is False
    assert updated.last_permission_check is None


def test_bulk_status_change_is_atomic(session) -> None:
    first = create_test_destination(session)
    second = DestinationService(session).create(
        DestinationCreate(
            name="Second Test Channel",
            telegram_chat_id="-100999",
            destination_type=DestinationType.TEST_CHANNEL,
            source_code="test_second",
            is_test=True,
        )
    )

    items = DestinationService(session).set_bulk_status(
        [first.id, second.id], RecordStatus.DISABLED, "operator-1"
    )

    assert {item.status for item in items} == {RecordStatus.DISABLED}


def test_bulk_status_rejects_unknown_id_without_partial_change(session) -> None:
    destination = create_test_destination(session)

    with pytest.raises(DomainError) as exc_info:
        DestinationService(session).set_bulk_status(
            [destination.id, "missing-id"], RecordStatus.DISABLED, "operator-1"
        )

    session.refresh(destination)
    assert exc_info.value.code == "DESTINATION_NOT_FOUND"
    assert destination.status == RecordStatus.ENABLED
