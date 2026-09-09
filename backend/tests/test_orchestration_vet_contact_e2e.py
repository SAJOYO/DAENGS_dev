"""응급 발화가 엔진을 지나 사용자 문장까지 오는 길."""

from __future__ import annotations

import httpx
from daengs_backend.orchestration.adapters.vet_contact import VetContactCapabilityAdapter
from daengs_backend.orchestration.contracts import (
    AssistantStatus,
    CapabilityName,
    PrincipalContext,
)
from daengs_backend.orchestration.graph import OrchestrationEngine
from daengs_backend.orchestration.planner import resolve_emergency_route

SEOUL = {"location": {"lat": 37.5665, "lon": 126.978}}
PRINCIPAL = PrincipalContext(subject="test-user", kind="APP_USER")


def test_engine_registers_the_capability_by_default() -> None:
    engine = OrchestrationEngine()
    assert CapabilityName.VET_CONTACT in engine._adapters


async def test_emergency_utterance_answers_with_phone_numbers() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "groups": [
                    {
                        "kind": "hospital",
                        "limit": 5,
                        "truncated": False,
                        "results": [
                            {
                                "place": {
                                    "key": {"source": "public:mois:animal_hospital", "ref": "1"},
                                    "name": "가까운동물병원",
                                    "lat": 37.5,
                                    "lng": 127.0,
                                    "distance_m": 320,
                                    "match": {"kind": "hospital"},
                                    "classifications": [{"source": {"source": "s", "ref": "r"}}],
                                    "facts": {
                                        "address": "서울시 중구",
                                        "phone": "02-123-4567",
                                        "medical": {"active": True, "open_now": None},
                                    },
                                },
                                "evaluations": {},
                            }
                        ],
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        plan = resolve_emergency_route(
            query="강아지가 초콜릿을 먹었어요",
            context=SEOUL,
            requested_capability=None,
            at_night=False,
        )
        engine = OrchestrationEngine(
            adapters={CapabilityName.VET_CONTACT: VetContactCapabilityAdapter(client=client)}
        )
        response = await engine.run(
            route_plan=plan,
            query="강아지가 초콜릿을 먹었어요",
            principal=PRINCIPAL,
            request_id="request-e2e",
            context=SEOUL,
        )
    finally:
        await client.aclose()

    assert response.status is AssistantStatus.ANSWERED
    assert response.message.startswith("응급 상황으로 보여요.")
    assert "확인해 드릴 수 없습니다" in response.message
    [result] = response.results
    assert result.data["candidates"][0]["phone"] == "02-123-4567"


async def test_missing_location_is_uncertain_and_carries_the_cta_code() -> None:
    plan = resolve_emergency_route(
        query="강아지가 초콜릿을 먹었어요",
        context={},
        requested_capability=None,
        at_night=False,
    )
    engine = OrchestrationEngine(
        adapters={CapabilityName.VET_CONTACT: VetContactCapabilityAdapter()}
    )
    response = await engine.run(
        route_plan=plan,
        query="강아지가 초콜릿을 먹었어요",
        principal=PRINCIPAL,
        request_id="request-nolocation",
    )
    assert response.status is AssistantStatus.UNCERTAIN
    assert response.results[0].abstention.code == "vet_contact.location_required"
    assert "위도" not in response.message
