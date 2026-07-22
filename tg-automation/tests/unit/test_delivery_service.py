from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from tests.unit.test_campaign_service import build_approved_campaign
from tg_automation.campaigns.service import CampaignService
from tg_automation.core.time import utc_now
from tg_automation.deliveries.service import DeliveryService
from tg_automation.storage.enums import CampaignStatus, DeliveryStatus, RecordStatus
from tg_automation.storage.models import MessageDelivery, TelegramDestination
from tg_automation.telegram.schemas import TelegramSendResult


@dataclass
class FakeGateway:
    message_id: int = 777
    calls: int = 0

    async def send_photo(self, chat_id, photo, caption, buttons):
        self.calls += 1
        return TelegramSendResult(chat_id=chat_id, message_id=self.message_id)


async def test_worker_claims_and_sends_delivery(session) -> None:
    campaign, service = build_approved_campaign(session)
    CampaignService(session).send_now(campaign.id, sending_enabled=True)
    worker = DeliveryService(session, "worker-test")

    ids = worker.claim_ready()
    delivery = await worker.process(ids[0], FakeGateway())

    session.refresh(campaign)
    assert delivery.status == DeliveryStatus.SENT
    assert delivery.telegram_message_id == 777
    assert campaign.status == CampaignStatus.SENT


def test_worker_recovers_delivery_after_lease_expires(session) -> None:
    campaign, _service = build_approved_campaign(session)
    CampaignService(session).send_now(campaign.id, sending_enabled=True)

    first_worker = DeliveryService(session, "worker-one")
    delivery_id = first_worker.claim_ready()[0]
    delivery = session.get(MessageDelivery, delivery_id)
    delivery.lease_expires_at = utc_now() - timedelta(seconds=1)
    session.commit()

    second_worker = DeliveryService(session, "worker-two")
    reclaimed = second_worker.claim_ready()

    session.refresh(delivery)
    assert reclaimed == [delivery_id]
    assert delivery.status == DeliveryStatus.SENDING
    assert delivery.locked_by == "worker-two"


async def test_worker_does_not_send_to_destination_disabled_after_scheduling(session) -> None:
    campaign, _service = build_approved_campaign(session)
    CampaignService(session).send_now(campaign.id, sending_enabled=True)
    worker = DeliveryService(session, "worker-test")
    delivery_id = worker.claim_ready()[0]
    destination = session.query(TelegramDestination).one()
    destination.status = RecordStatus.DISABLED
    session.commit()
    gateway = FakeGateway()

    delivery = await worker.process(delivery_id, gateway)

    assert delivery.status == DeliveryStatus.FAILED
    assert delivery.error_code == "DESTINATION_UNAVAILABLE"
    assert gateway.calls == 0
    assert delivery.telegram_message_id is None
