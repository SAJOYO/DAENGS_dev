"""HTTP transport and CapabilityResult mapping for the Place capability boundary."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import httpx
from daengs_backend.orchestration.adapters.place import PlaceCapabilityAdapter
from daengs_backend.orchestration.contracts import (
    CapabilityRequest,
    CapabilityStatus,
    PlacePayload,
)

from place_capability_cases import discovery_payload


def _request() -> CapabilityRequest:
    return CapabilityRequest(
        capability="place",
        payload=PlacePayload(query="  강아지 장난감 사고 싶어  ", lat=37.5563, lon=126.9236),
    )


async def test_adapter_posts_only_original_query_location_and_server_policy() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=discovery_payload())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await PlaceCapabilityAdapter(
            client=client, base_url="http://place-search:8000"
        ).run(_request(), request_id="request-123")
    finally:
        await client.aclose()

    assert result.status is CapabilityStatus.OK
    [sent] = seen
    assert sent.url == "http://place-search:8000/internal/place/discovery"
    assert sent.headers["X-Request-ID"] == "request-123"
    assert json.loads(sent.content) == {
        "query": "  강아지 장난감 사고 싶어  ",
        "spatial": {"lat": 37.5563, "lng": 126.9236, "radius_m": 3000},
        "conditions": None,
    }


async def test_empty_but_valid_result_is_abstained_with_information() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=discovery_payload(
                status="needs_clarification", group_sizes=(), refinement=True
            ),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await PlaceCapabilityAdapter(
            client=client, base_url="http://place-search:8000"
        ).run(_request(), request_id="request-123")
    finally:
        await client.aclose()

    assert result.status is CapabilityStatus.ABSTAINED
    assert result.abstention is not None
    assert result.abstention.code == "place_needs_clarification"
    assert result.data is not None and result.data["refinements"]


async def test_timeout_and_provider_errors_are_stable_and_do_not_leak_bodies() -> None:
    async def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("secret timeout", request=request)

    timeout_client = httpx.AsyncClient(transport=httpx.MockTransport(timeout))
    try:
        timed_out = await PlaceCapabilityAdapter(
            client=timeout_client, base_url="http://place-search:8000"
        ).run(_request(), request_id="request-123")
    finally:
        await timeout_client.aclose()
    assert timed_out.status is CapabilityStatus.TIMEOUT
    assert timed_out.error is not None and timed_out.error.kind == "place_discovery_timeout"
    assert "secret" not in timed_out.error.detail

    async def failed(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="secret provider body")

    failure_client = httpx.AsyncClient(transport=httpx.MockTransport(failed))
    try:
        failure = await PlaceCapabilityAdapter(
            client=failure_client, base_url="http://place-search:8000"
        ).run(_request(), request_id="request-123")
    finally:
        await failure_client.aclose()
    assert failure.status is CapabilityStatus.ERROR
    assert failure.error is not None
    assert failure.error.kind == "place_discovery_upstream_failure"
    assert "secret" not in failure.error.detail


async def test_internal_provider_envelopes_keep_configuration_and_timeout_distinct() -> None:
    responses = [
        httpx.Response(
            503,
            json={
                "detail": {
                    "code": "place_intent_not_configured",
                    "message": "secret internal detail",
                }
            },
        ),
        httpx.Response(
            504,
            json={
                "detail": {
                    "code": "place_intent_provider_timeout",
                    "message": "secret internal detail",
                }
            },
        ),
    ]

    async def handler(_request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = PlaceCapabilityAdapter(client=client, base_url="http://place-search:8000")
    try:
        not_configured = await adapter.run(_request(), request_id="request-123")
        timed_out = await adapter.run(_request(), request_id="request-124")
    finally:
        await client.aclose()

    assert not_configured.status is CapabilityStatus.ERROR
    assert not_configured.error is not None
    assert not_configured.error.kind == "place_discovery_not_configured"
    assert timed_out.status is CapabilityStatus.TIMEOUT
    assert timed_out.error is not None and timed_out.error.kind == "place_discovery_timeout"
    assert "secret" not in not_configured.error.detail + timed_out.error.detail


async def test_invalid_success_body_is_a_sanitized_capability_error() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"raw": "secret provider output"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await PlaceCapabilityAdapter(
            client=client, base_url="http://place-search:8000"
        ).run(_request(), request_id="request-123")
    finally:
        await client.aclose()

    assert result.status is CapabilityStatus.ERROR
    assert result.error is not None
    assert result.error.kind == "place_discovery_invalid_response"
    assert "secret" not in result.error.detail


def test_backend_place_adapter_never_imports_place_implementation() -> None:
    adapter_dir = (
        Path(__file__).parents[1]
        / "src"
        / "daengs_backend"
        / "orchestration"
        / "adapters"
    )
    imports: set[str] = set()
    for filename in ("place.py", "_place_contract.py", "_place_projection.py"):
        tree = ast.parse((adapter_dir / filename).read_text(encoding="utf-8-sig"))
        imports.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        imports.update(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        )
    assert not {name for name in imports if name.startswith("daengs_place")}


