from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from tests.unit.test_campaign_service import build_approved_campaign
from tg_automation.campaigns.service import CampaignService
from tg_automation.core.errors import DomainError
from tg_automation.core.time import utc_now
from tg_automation.operations.service import OperationsService
from tg_automation.storage.enums import CampaignStatus, DeliveryStatus
from tg_automation.storage.models import (
    AuditLog,
    ContentItem,
    MessageDelivery,
)


def failed_delivery(session):
    campaign, _service = build_approved_campaign(session)
    CampaignService(session).send_now(campaign.id, sending_enabled=True)
    delivery = session.scalar(
        select(MessageDelivery).where(MessageDelivery.campaign_id == campaign.id)
    )
    delivery.status = DeliveryStatus.FAILED
    delivery.error_code = "TELEGRAM_NETWORK_ERROR"
    delivery.error_message = "network unavailable"
    delivery.attempt_count = 3
    campaign.status = CampaignStatus.FAILED
    session.commit()
    return campaign, delivery


def test_operator_can_retry_failed_public_delivery(session) -> None:
    campaign, delivery = failed_delivery(session)

    result = OperationsService(session).retry_public_delivery(delivery.id, "operator-1")

    session.refresh(campaign)
    assert result.status == DeliveryStatus.PENDING
    assert result.attempt_count == 0
    assert result.error_code is None
    assert campaign.status == CampaignStatus.SCHEDULED
    audit = session.scalar(select(AuditLog).where(AuditLog.action == "DELIVERY_MANUAL_RETRY"))
    assert audit.actor_id == "operator-1"


def test_non_failed_delivery_cannot_be_retried(session) -> None:
    campaign, _service = build_approved_campaign(session)
    CampaignService(session).send_now(campaign.id, sending_enabled=True)
    delivery = session.scalar(
        select(MessageDelivery).where(MessageDelivery.campaign_id == campaign.id)
    )

    with pytest.raises(DomainError) as exc_info:
        OperationsService(session).retry_public_delivery(delivery.id, "operator-1")

    assert exc_info.value.code == "DELIVERY_NOT_RETRYABLE"


def test_expired_content_cannot_be_retried(session) -> None:
    campaign, delivery = failed_delivery(session)
    content = session.get(ContentItem, campaign.content_id)
    content.valid_until = utc_now() - timedelta(minutes=1)
    session.commit()

    with pytest.raises(DomainError) as exc_info:
        OperationsService(session).retry_public_delivery(delivery.id, "operator-1")

    assert exc_info.value.code == "CONTENT_EXPIRED"


def test_queue_overview_hides_test_destinations_by_default(session) -> None:
    campaign, _service = build_approved_campaign(session)
    CampaignService(session).send_now(campaign.id, sending_enabled=True)
    service = OperationsService(session)

    public_only = service.queue_overview()
    including_tests = service.queue_overview(include_test=True)

    assert public_only["counts"]["pending"] == 0
    assert including_tests["counts"]["pending"] == 1
