import pytest


@pytest.mark.asyncio
async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_legacy_chat_route_disabled_by_default(client):
    response = await client.post("/api/v1/chat", json={"message": ""})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_legacy_status_route_disabled_by_default(client):
    response = await client.get("/api/v1/status")
    assert response.status_code == 404
