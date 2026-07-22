from __future__ import annotations

from tests.unit.test_analytics_service import seed_real_and_test_metrics


async def test_analytics_api_defaults_to_production_only(client, session) -> None:
    seed_real_and_test_metrics(session)

    production = await client.get("/api/v1/tg/analytics/overview")
    with_test = await client.get("/api/v1/tg/analytics/overview?include_test=true")

    assert production.status_code == 200
    assert production.json()["data"]["link_clicks"] == 1
    assert with_test.json()["data"]["link_clicks"] == 2
