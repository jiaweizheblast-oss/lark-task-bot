from __future__ import annotations

from sqlalchemy import select

from tests.unit.test_campaign_service import build_approved_campaign
from tg_automation.campaigns.service import CampaignService
from tg_automation.core.config import clear_settings_cache
from tg_automation.storage.enums import CampaignStatus, DeliveryStatus
from tg_automation.storage.models import MessageDelivery


def headers(key: str, actor: str) -> dict[str, str]:
    return {"X-NEXUS-API-KEY": key, "X-NEXUS-ACTOR": actor}


async def test_operations_api_lists_and_retries_failed_delivery(
    client, session, monkeypatch
) -> None:
    campaign, _service = build_approved_campaign(session)
    CampaignService(session).send_now(campaign.id, sending_enabled=True)
    delivery = session.scalar(
        select(MessageDelivery).where(MessageDelivery.campaign_id == campaign.id)
    )
    delivery.status = DeliveryStatus.FAILED
    delivery.error_code = "TELEGRAM_NETWORK_ERROR"
    campaign.status = CampaignStatus.FAILED
    session.commit()

    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.setenv("NEXUS_OPERATOR_API_KEY", "operator-key")
    clear_settings_cache()
    try:
        queue = await client.get(
            "/api/v1/tg/operations/queue?include_test=true",
            headers=headers("operator-key", "operator-1"),
        )
        records = await client.get(
            f"/api/v1/tg/operations/campaigns/{campaign.id}/deliveries",
            headers=headers("operator-key", "operator-1"),
        )
        denied = await client.post(
            f"/api/v1/tg/operations/deliveries/{delivery.id}/retry",
            headers=headers("invalid-key", "unknown"),
        )
        retried = await client.post(
            f"/api/v1/tg/operations/deliveries/{delivery.id}/retry",
            headers=headers("operator-key", "operator-1"),
        )
    finally:
        clear_settings_cache()

    assert queue.status_code == 200
    assert queue.json()["data"]["counts"]["failed"] == 1
    assert records.status_code == 200
    assert records.json()["data"][0]["delivery_id"] == delivery.id
    assert denied.status_code == 401
    assert retried.status_code == 200
    assert retried.json()["data"]["status"] == "PENDING"
