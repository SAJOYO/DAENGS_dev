"""The standalone web host reads the existing account profiles without editing them."""

import httpx
import pytest
import serve


@pytest.mark.asyncio
async def test_profiles_use_existing_authenticated_api_and_context_changes(monkeypatch):
    original_client = httpx.AsyncClient
    token = "test-token-a"
    calls = []

    def backend(request):
        calls.append(request)
        return httpx.Response(200, json={"pets": [{"id": "a", "weight_kg": "5.0"}]})

    monkeypatch.setattr(
        serve, "config", lambda: {"backend_url": "http://backend", "access_token": token}
    )
    monkeypatch.setattr(
        serve.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(backend), **kwargs),
    )
    async with original_client(
        transport=httpx.ASGITransport(app=serve.app), base_url="http://testserver"
    ) as client:
        first = await client.get("/api/live/profiles")
        same = await client.get("/api/live/profiles")
        token = "test-token-b"
        changed = await client.get("/api/live/profiles")
    assert first.status_code == 200
    assert first.json()["pets"][0]["weight_kg"] == "5.0"
    assert first.json()["owner_context"] == same.json()["owner_context"]
    assert first.json()["owner_context"] != changed.json()["owner_context"]
    assert all(r.method == "GET" and r.url.path == "/app/pets" for r in calls)
    assert calls[0].headers["authorization"] == "Bearer test-token-a"
    assert calls[-1].headers["authorization"] == "Bearer test-token-b"
    assert "test-token" not in first.text


@pytest.mark.asyncio
async def test_missing_connection_does_not_return_fixture_pets(monkeypatch):
    monkeypatch.setattr(serve, "config", dict)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=serve.app), base_url="http://testserver"
    ) as client:
        response = await client.get("/api/live/profiles")
        fixture = await client.get("/api/fixture/profiles")
    assert response.status_code == 503
    assert "pets" not in response.json()
    assert len(fixture.json()["pets"]) == 3
