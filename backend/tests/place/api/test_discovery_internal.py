import json

import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from daengs_place.api import discovery_internal
from daengs_place.core.config import settings
from daengs_place.core.db import get_session
from daengs_place.main import app
from daengs_place.place.discovery.service import PlaceDiscoveryService
from daengs_place.place.intent.service import PlaceIntentSuggestionService
from daengs_place.place.providers.gemini import GeminiIntentProposer


async def _no_db():
    yield None


def _body() -> dict:
    return {
        "query": "조용한 곳",
        "spatial": {"lat": 37.5563, "lng": 126.9236, "radius_m": 3000},
    }


def _completed(output: dict) -> dict:
    return {
        "status": "completed",
        "steps": [
            {
                "type": "model_output",
                "content": [{"type": "text", "text": json.dumps(output)}],
            }
        ],
    }


def _service_with_transport(handler) -> PlaceDiscoveryService:
    proposer = GeminiIntentProposer(
        "test-key",
        "test-model",
        transport=httpx.MockTransport(handler),
    )
    return PlaceDiscoveryService(PlaceIntentSuggestionService(proposer))


def _post_with_factory(monkeypatch, factory):
    monkeypatch.setattr(discovery_internal, "get_place_discovery_service", factory)
    app.dependency_overrides[get_session] = _no_db
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            return client.post("/internal/place/discovery", json=_body())
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_missing_key_fails_only_internal_discovery(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", SecretStr(""))
    app.dependency_overrides[get_session] = _no_db
    try:
        with TestClient(app) as client:
            assert client.get("/health").json() == {"ok": True}
            rejected = client.post(
                "/v2/places/search",
                json={"lat": 37.5, "lng": 127.0, "kinds": ["cafe"], "conditions": {}},
            )
            response = client.post("/internal/place/discovery", json=_body())
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert rejected.status_code == 422
    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "place_intent_not_configured",
        "message": "Place discovery is not configured",
    }


def test_timeout_and_provider_failure_have_stable_sanitized_codes(monkeypatch):
    async def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("do not expose", request=request)

    response = _post_with_factory(
        monkeypatch,
        lambda: _service_with_transport(timeout),
    )
    assert response.status_code == 504
    assert response.json()["detail"]["code"] == "place_intent_provider_timeout"
    assert "expose" not in response.text

    async def failed(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="do not expose")

    response = _post_with_factory(
        monkeypatch,
        lambda: _service_with_transport(failed),
    )
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "place_intent_provider_failure"
    assert "expose" not in response.text


def test_invalid_provider_schema_becomes_non_executable_domain_result(monkeypatch):
    async def invalid(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_completed(
                {
                    "disposition": "proposed",
                    "reason": "none",
                    "interpretations": [{"proposals": []}],
                }
            ),
        )

    response = _post_with_factory(
        monkeypatch,
        lambda: _service_with_transport(invalid),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["planning"]["status"] == "needs_clarification"
    assert payload["planning"]["issues"][0]["code"] == "intent_proposer_invalid_output"
    assert payload["lens_results"] == []
    assert "raw" not in response.text
    assert "proposals" not in response.text


def test_internal_request_cannot_override_server_result_policy(monkeypatch):
    body = _body()
    body["result_policy"] = {"max_total_candidates": 20}

    def validation_must_run_first():
        raise AssertionError("validation must run before service construction")

    monkeypatch.setattr(
        discovery_internal,
        "get_place_discovery_service",
        validation_must_run_first,
    )
    app.dependency_overrides[get_session] = _no_db
    try:
        with TestClient(app) as client:
            rejected = client.post("/internal/place/discovery", json=body)
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert rejected.status_code == 422
    assert "result_policy" in rejected.text
