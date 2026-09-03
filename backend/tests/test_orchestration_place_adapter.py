"""Consumer contract for the backend → place-search HTTP capability boundary."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import httpx

from daengs_backend.orchestration.adapters.place import (
    MAX_PLACE_CAPABILITY_BYTES,
    PlaceCapabilityAdapter,
)
from daengs_backend.orchestration.contracts import (
    CapabilityRequest,
    CapabilityStatus,
    PlacePayload,
)


def _fact(index: int, *, borrowed: bool = False, large: bool = False) -> dict:
    source = "kcisa" if borrowed else "kto"
    return {
        "fact_id": f"amenities.fact-{index}",
        "label": f"정보 {index}",
        "display_text": ("상세 정보 " + "가" * 480) if large else "반려견 용품을 구매할 수 있어요.",
        "source_state": "known",
        "evaluation_state": "not_evaluated",
        "severity": "info",
        "provenance": {
            "source": {"source": source, "ref": f"source-{index}"},
            "source_role": "supporting" if borrowed else "primary",
            "value_origin": "borrowed" if borrowed else "own",
            "link": {"state": "candidate"} if borrowed else None,
        },
    }


def _candidate(ref: str, *, large: bool = False) -> tuple[dict, dict]:
    key = {"source": "kto", "ref": ref}
    hit = {"place": {"key": key, "lat": 37.556, "lng": 126.923}}
    presentation = {
        "place_key": key,
        "title": ("테스트 장소 " + "나" * 280) if large else f"테스트 장소 {ref}",
        "summary": ("요약 " + "다" * 490) if large else "가까운 반려동물 동반 장소예요.",
        "kind_id": "pet_shop",
        "kind_label": "펫샵",
        "distance_m": 420,
        "address": ("서울 " + "라" * 490) if large else "서울 마포구",
        "core_items": [_fact(90, large=large)],
        "promoted_items": [_fact(index, borrowed=index == 1, large=large) for index in range(5)],
        "detail_items": [_fact(index + 10, large=large) for index in range(5)],
        "notices": [
            {"code": f"candidate.notice-{index}", "message": "확인이 필요한 정보예요.", "severity": "warning"}
            for index in range(4)
        ],
        "why_matched": [
            {"code": f"matched.reason-{index}", "message": "요청한 용품 정보가 있어요."}
            for index in range(4)
        ],
        # These representative internal-only fields must disappear at the adapter.
        "source_evidence": [{"raw": "must-not-cross"}],
        "policy_receipt": {"internal_trace": "must-not-cross"},
    }
    return hit, presentation


def discovery_payload(
    *,
    status: str = "ready",
    mapping_scope: str = "direct",
    group_sizes: tuple[int, ...] = (1,),
    deferred: bool = False,
    refinement: bool = False,
    issue: str | None = None,
    large: bool = False,
) -> dict:
    targets = []
    lens_results = []
    for lens_index, size in enumerate(group_sizes, start=1):
        lens_id = f"target:{lens_index}"
        targets.append(
            {
                "lens_id": lens_id,
                "display_label": f"#방향{lens_index}",
                "mapping_scope": mapping_scope,
                "availability": "executable",
                "support_note": "가능한 장소 유형을 기준으로 찾았어요.",
            }
        )
        pairs = [_candidate(f"K{lens_index}-{index}", large=large) for index in range(size)]
        lens_results.append(
            {
                "lens_id": lens_id,
                "display_label": f"#방향{lens_index}",
                "support_note": "가능한 장소 유형을 기준으로 찾았어요.",
                "search": {"groups": [{"results": [hit for hit, _ in pairs]}]},
                "presentations": [presentation for _, presentation in pairs],
            }
        )
    signals = []
    if deferred:
        signals.append(
            {
                "lens_id": "signal:quiet",
                "display_label": "#조용한 분위기",
                "availability": "deferred",
                "required": False,
                "support_note": "조용함을 판정할 장소 근거가 아직 없어요.",
                "options": [],
            }
        )
    if refinement:
        signals.append(
            {
                "lens_id": "signal:price",
                "display_label": "#비용 기준",
                "availability": "needs_selection",
                "required": False,
                "support_note": "어떤 비용을 뜻하는지 골라주세요.",
                "options": [
                    {
                        "option_id": "cost.travel_distance",
                        "display_label": "이동 거리",
                        "availability": "proxy",
                        "support_note": "가까운 곳을 우선해요.",
                    }
                ],
            }
        )
    return {
        "contract_version": "place-discovery-v1",
        "planning": {
            "contract_version": "place-discovery-planning-v1",
            "status": status,
            "source_disposition": "proposed" if status == "ready" else "abstained",
            "resolution": "exploratory" if status == "ready" else None,
            "lenses": {"target_lenses": targets, "signal_lenses": signals},
            "issues": [{"code": issue, "blocking": False}] if issue else [],
        },
        "result_policy": {"raw": "must-not-cross"},
        "lens_results": lens_results if status == "ready" else [],
        "notices": [],
    }


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


async def test_projection_preserves_source_identity_but_drops_internal_material() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=discovery_payload())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await PlaceCapabilityAdapter(
            client=client, base_url="http://place-search:8000"
        ).run(_request(), request_id="request-123")
    finally:
        await client.aclose()

    assert result.data is not None
    candidate = result.data["groups"][0]["candidates"][0]
    assert candidate["place_id"] == {"source": "kto", "ref": "K1-0"}
    borrowed = next(fact for fact in candidate["facts"] if fact["id"] == "amenities.fact-1")
    assert borrowed["provenance"] == {
        "source": "kcisa",
        "ref": "source-1",
        "role": "supporting",
        "value_origin": "borrowed",
        "link_state": "candidate",
    }
    encoded = json.dumps(result.data, ensure_ascii=False)
    assert "must-not-cross" not in encoded
    assert "source_evidence" not in encoded
    assert "policy_receipt" not in encoded


async def test_fallback_and_unresolved_signal_are_disclosed_without_claiming_a_match() -> None:
    payload = discovery_payload(
        mapping_scope="product_fallback",
        deferred=True,
        issue="unsupported_semantic_intent",
    )

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await PlaceCapabilityAdapter(
            client=client, base_url="http://place-search:8000"
        ).run(_request(), request_id="request-123")
    finally:
        await client.aclose()

    assert result.status is CapabilityStatus.OK
    assert result.data is not None
    assert "직접 확인할 근거가 부족" in result.data["answer"]
    notices = result.data["notices"]
    assert {notice["code"] for notice in notices} >= {
        "unsupported_semantic_intent",
        "place.signal_deferred",
    }
    assert "조용한 장소" not in result.data["answer"]


async def test_explicit_open_discovery_is_not_mislabeled_as_an_unsupported_fallback() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=discovery_payload(mapping_scope="open_discovery"))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await PlaceCapabilityAdapter(
            client=client, base_url="http://place-search:8000"
        ).run(_request(), request_id="request-123")
    finally:
        await client.aclose()

    assert result.data is not None
    assert "직접 확인할 근거가 부족" not in result.data["answer"]
    assert "가까운 장소 1곳" in result.data["answer"]


async def test_needs_selection_survives_as_an_actionable_refinement() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=discovery_payload(refinement=True))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await PlaceCapabilityAdapter(
            client=client, base_url="http://place-search:8000"
        ).run(_request(), request_id="request-123")
    finally:
        await client.aclose()

    assert result.data is not None
    [refinement] = result.data["refinements"]
    assert refinement["lens_id"] == "signal:price"
    assert refinement["options"][0]["id"] == "cost.travel_distance"


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


async def test_projection_has_an_independent_byte_budget() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=discovery_payload(group_sizes=(5, 5, 5), large=True),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await PlaceCapabilityAdapter(
            client=client, base_url="http://place-search:8000"
        ).run(_request(), request_id="request-123")
    finally:
        await client.aclose()

    assert result.status is CapabilityStatus.OK
    assert result.data is not None
    encoded = json.dumps(result.data, ensure_ascii=False, separators=(",", ":")).encode()
    assert len(encoded) <= MAX_PLACE_CAPABILITY_BYTES
    assert sum(len(group["candidates"]) for group in result.data["groups"]) <= 9
    assert "place.capability_projection_applied" in {
        notice["code"] for notice in result.data["notices"]
    }


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
    source_path = (
        Path(__file__).parents[1]
        / "src"
        / "daengs_backend"
        / "orchestration"
        / "adapters"
        / "place.py"
    )
    tree = ast.parse(source_path.read_text(encoding="utf-8-sig"))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports.update(
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )
    assert not {name for name in imports if name.startswith("daengs_place")}
