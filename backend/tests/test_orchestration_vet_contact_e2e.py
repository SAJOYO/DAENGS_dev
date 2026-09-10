"""응급 발화가 엔진을 지나 사용자 문장까지 오는 길."""

from __future__ import annotations

import httpx
import pytest

from daengs_backend.orchestration.adapters.vet_contact import VetContactCapabilityAdapter
from daengs_backend.orchestration.contracts import (
    AssistantResponse,
    AssistantStatus,
    CapabilityName,
    PrincipalContext,
)
from daengs_backend.orchestration.graph import OrchestrationEngine
from daengs_backend.orchestration.planner import resolve_emergency_route
from daengs_backend.orchestration.semantic import GeminiSemanticRouter
from daengs_backend.orchestration.service import AssistantOrchestrationService

# `agent` extra 는 기본 설치에 없다 — `tests/test_orchestration_agent.py` 와 같은 이유·같은
# 자리에서 같은 방식으로 가드한다. 이 파일의 다른 테스트는 langchain 없이도 돌아야 하므로
# 모듈 전체를 `importorskip` 하지 않고, 아래 테스트 하나만 조건부로 건너뛴다.
try:
    from langchain_core.language_models import BaseChatModel

    from daengs_backend.orchestration.agent.service import AgentOrchestrationService

    _HAS_AGENT = True
except ImportError:  # pragma: no cover - 개발 PC 의 기본 설치
    _HAS_AGENT = False

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


async def test_emergency_utterance_never_reaches_the_semantic_router() -> None:
    """응급 게이트는 서비스 계층(`AssistantOrchestrationService.run`)에서도 라우터보다
    앞이어야 한다. 지금까지의 테스트는 전부 `resolve_emergency_route` 를 직접 부르거나
    `engine.run` 부터 시작해서, 게이트가 라우터 뒤로 옮겨져도 초록으로 남는다 — 이 테스트가
    그 구멍을 막는다. 라우터의 `select` 는 불리면 예외를 던진다."""

    async def generate(_prompt: str) -> object:
        raise AssertionError("응급 발화인데 시맨틱 라우터가 불렸다")

    class FakeEngine:
        async def run(self, *, route_plan, query, principal, request_id, **_):
            assert route_plan is not None
            assert route_plan.requests and route_plan.requests[0].capability == (
                CapabilityName.VET_CONTACT
            )
            return AssistantResponse(
                request_id=request_id,
                status=AssistantStatus.ANSWERED,
                message="응급 상황으로 보여요.",
                results=[],
                handoffs=[],
                clarify=None,
                route=None,
            )

    service = AssistantOrchestrationService(
        engine=FakeEngine(), semantic_router=GeminiSemanticRouter(generate=generate)
    )
    response = await service.run(
        query="강아지가 갑자기 숨을 헐떡이면서 거품을 물고 쓰러졌어요!",
        principal=PRINCIPAL,
        context=SEOUL,
    )

    assert response.status is AssistantStatus.ANSWERED
    assert response.message == "응급 상황으로 보여요."


@pytest.mark.skipif(not _HAS_AGENT, reason="agent extra(langchain) 가 안 깔려 있다")
async def test_emergency_utterance_never_reaches_the_agent_model() -> None:
    """위 `..._never_reaches_the_semantic_router` 의 짝 — 에이전트 오케스트레이터
    (`AgentOrchestrationService`) 쪽 응급 게이트를 잰다. `agent/service.py` 는 LangGraph
    쪽(`service.py`)과 같은 자리·같은 이유로 응급을 `_plan_and_execute` 맨 앞에서 거른다고
    적어 뒀지만, 그것을 실제로 재는 테스트는 지금까지 없었다 — 두 호출을 순서만 바꿔도
    스위트 전체가 초록으로 남는다. 모델이 불리면 예외를 던진다."""

    class ExplodingAgentModel(BaseChatModel):
        """`create_agent` 가 요구하는 최소 `BaseChatModel`. 불리면 즉시 실패한다."""

        @property
        def _llm_type(self) -> str:
            return "exploding"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            raise AssertionError("응급 발화인데 에이전트 모델이 불렸다")

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
        service = AgentOrchestrationService(
            model=ExplodingAgentModel(),
            engine=OrchestrationEngine(
                adapters={CapabilityName.VET_CONTACT: VetContactCapabilityAdapter(client=client)}
            ),
        )
        response = await service.run(
            query="강아지가 갑자기 숨을 헐떡이면서 거품을 물고 쓰러졌어요!",
            principal=PRINCIPAL,
            context=SEOUL,
        )
    finally:
        await client.aclose()

    assert response.status is AssistantStatus.ANSWERED
    assert response.message.startswith("응급 상황으로 보여요.")
    [result] = response.results
    assert result.data["candidates"][0]["phone"] == "02-123-4567"
