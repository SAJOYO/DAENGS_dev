"""Focused E2E: real HTTP → real auth → real `AssistantOrchestrationService` →
real planner → real Card 1 graph → real aggregation.

Only two boundaries are faked: the Gemini transport (`ScriptedTransport`) and the
capability adapters (`RecordingAdapter`) — the seam Card 1/Card 2B already treat as
external (provider/domain implementation). Everything above that — HTTP routing,
auth, DTO validation, `PrincipalContext` construction, `resolve_deterministic_route`,
`assemble_route_plan`, `OrchestrationEngine`, `aggregate_results` — is the real
production code, wired through `assistant_router.get_assistant_orchestration_service`
exactly as the endpoint constructs it in production, just with a controlled engine.

This does not retest Card 3's DTO validation (`test_assistant_api.py`) or Card 2B's
routing-semantics unit tests (`test_orchestration_semantic_router.py`) — it proves
the chain between them is actually connected.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi.testclient import TestClient

from daengs_backend.core.subject import SubjectType
from daengs_backend.core.token import create_access_token
from daengs_backend.orchestration.contracts import (
    CapabilityName,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
)
from daengs_backend.orchestration.graph import OrchestrationEngine
from daengs_backend.orchestration.semantic import GeminiSemanticRouter
from daengs_backend.orchestration.service import AssistantOrchestrationService
from daengs_backend.routers import assistant as assistant_router

QUERY = "우리 개가 자꾸 손을 물어요. 어떻게 훈련해야 하나요?"


class RecordingAdapter:
    """Fakes only the provider/domain boundary — records what the real graph sent it."""

    def __init__(self, capability: CapabilityName, result: CapabilityResult) -> None:
        self.capability = capability
        self.calls: list[CapabilityRequest] = []
        self._result = result

    async def run(self, request: CapabilityRequest, *, request_id: str) -> CapabilityResult:
        self.calls.append(request)
        return self._result


class ScriptedTransport:
    """Fakes only the Gemini call — everything after the raw output is real."""

    def __init__(self, *outputs: object) -> None:
        self.outputs = list(outputs)
        self.prompts: list[str] = []

    async def __call__(self, prompt: str) -> object:
        self.prompts.append(prompt)
        result = self.outputs.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _decision(execute: list[str] = (), handoffs: list[str] = ()) -> str:
    return json.dumps({"execute": list(execute), "handoffs": list(handoffs)})


def _ok(capability: CapabilityName, answer: str) -> CapabilityResult:
    return CapabilityResult(
        capability=capability, status=CapabilityStatus.OK, data={"answer": answer}, elapsed_ms=1
    )


def _app_token() -> str:
    return create_access_token(uuid.uuid4(), SubjectType.APP)


def _post(service: AssistantOrchestrationService, body: dict[str, Any], token: str | None) -> Any:
    """Real endpoint, real auth (a real bearer token, never overridden) — only the
    service the endpoint constructs via DI is swapped for a controlled-but-real one."""
    from daengs_backend.main import app

    app.dependency_overrides[assistant_router.get_assistant_orchestration_service] = lambda: service
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        with TestClient(app) as c:
            return c.post("/assistant/query", json=body, headers=headers)
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------- 1. Training EXECUTE


def test_training_execute_실제_그래프를_거쳐_실행된다() -> None:
    training = RecordingAdapter(
        CapabilityName.TRAINING, _ok(CapabilityName.TRAINING, "물기 교육 방법입니다")
    )
    transport = (
        ScriptedTransport()
    )  # any call would IndexError — deterministic route must skip Gemini
    service = AssistantOrchestrationService(
        engine=OrchestrationEngine({CapabilityName.TRAINING: training}),
        semantic_router=GeminiSemanticRouter(generate=transport),
    )
    got = _post(service, {"query": QUERY, "requested_capability": "training"}, _app_token())
    assert got.status_code == 200
    body = got.json()
    assert body["status"] == "ANSWERED"
    assert body["results"][0]["data"]["answer"] == "물기 교육 방법입니다"
    assert training.calls[0].payload.question == QUERY
    assert transport.prompts == []  # no semantic Gemini call


# ------------------------------------------------------------------------- 2. Life EXECUTE


def test_life_execute_실제_그래프를_거쳐_실행된다() -> None:
    life = RecordingAdapter(
        CapabilityName.LIFE, _ok(CapabilityName.LIFE, "동물등록 의무 안내입니다")
    )
    transport = ScriptedTransport()
    service = AssistantOrchestrationService(
        engine=OrchestrationEngine({CapabilityName.LIFE: life}),
        semantic_router=GeminiSemanticRouter(generate=transport),
    )
    got = _post(service, {"query": QUERY, "requested_capability": "life"}, _app_token())
    assert got.status_code == 200
    assert got.json()["status"] == "ANSWERED"
    assert life.calls[0].payload.question == QUERY
    assert transport.prompts == []


# ------------------------------------------------------------------- 3. Walk EXECUTE + 좌표


def test_walk_execute_신뢰된_좌표가_그대로_페이로드에_닿는다() -> None:
    walk = RecordingAdapter(CapabilityName.WALK, _ok(CapabilityName.WALK, "지금 산책하기 좋습니다"))
    transport = ScriptedTransport()
    service = AssistantOrchestrationService(
        engine=OrchestrationEngine({CapabilityName.WALK: walk}),
        semantic_router=GeminiSemanticRouter(generate=transport),
    )
    got = _post(
        service,
        {
            "query": QUERY,
            "requested_capability": "walk",
            "location": {"lat": 37.5665, "lon": 126.978},
        },
        _app_token(),
    )
    assert got.status_code == 200
    assert got.json()["status"] == "ANSWERED"
    assert walk.calls[0].payload.lat == 37.5665
    assert walk.calls[0].payload.lon == 126.978
    assert transport.prompts == []


# --------------------------------------------------------------- 4. Walk without location


def test_walk_좌표_없이_요청하면_아무것도_실행하지_않고_CLARIFY다() -> None:
    walk = RecordingAdapter(CapabilityName.WALK, _ok(CapabilityName.WALK, "실행되면 안 됨"))
    transport = ScriptedTransport()
    service = AssistantOrchestrationService(
        engine=OrchestrationEngine({CapabilityName.WALK: walk}),
        semantic_router=GeminiSemanticRouter(generate=transport),
    )
    got = _post(service, {"query": QUERY, "requested_capability": "walk"}, _app_token())
    assert got.status_code == 200
    body = got.json()
    assert body["status"] == "CLARIFY"
    assert body["results"] == [] and body["handoffs"] == []
    assert walk.calls == []
    assert transport.prompts == []  # deterministic signal — never reaches Gemini


# --------------------------------------------------------- 5. Skin HANDOFF (semantic path)


def test_skin_핸드오프는_의미_경로를_통해_실행_없이_고정_사유로_나온다() -> None:
    transport = ScriptedTransport(_decision(handoffs=["skin"]))
    service = AssistantOrchestrationService(
        engine=OrchestrationEngine({}),  # nothing should ever be asked to execute
        semantic_router=GeminiSemanticRouter(generate=transport),
    )
    got = _post(service, {"query": "우리 개 피부에 뭐가 났는데 봐줘"}, _app_token())
    assert got.status_code == 200
    body = got.json()
    assert body["status"] == "HANDOFF"
    assert body["handoffs"] == [{"target": "skin", "reason": "image_upload_required"}]
    assert body["results"] == []
    assert len(transport.prompts) == 1  # semantic path was actually used here


# --------------------------------------------------------- 6. Gait HANDOFF (semantic path)


def test_gait_핸드오프는_의미_경로를_통해_실행_없이_고정_사유로_나온다() -> None:
    transport = ScriptedTransport(_decision(handoffs=["gait"]))
    service = AssistantOrchestrationService(
        engine=OrchestrationEngine({}),
        semantic_router=GeminiSemanticRouter(generate=transport),
    )
    got = _post(
        service, {"query": "강아지 걷는 영상을 보고 보행 상태를 확인하고 싶어"}, _app_token()
    )
    assert got.status_code == 200
    body = got.json()
    assert body["status"] == "HANDOFF"
    assert body["handoffs"] == [{"target": "gait", "reason": "video_upload_required"}]
    assert body["results"] == []


# ------------------------------------------------------- 7. Mixed EXECUTE + HANDOFF


def test_training_실행과_gait_핸드오프가_공존하고_상태는_실행_결과에서만_나온다() -> None:
    training = RecordingAdapter(CapabilityName.TRAINING, _ok(CapabilityName.TRAINING, "훈련 답변"))
    transport = ScriptedTransport(_decision(execute=["training"], handoffs=["gait"]))
    service = AssistantOrchestrationService(
        engine=OrchestrationEngine({CapabilityName.TRAINING: training}),
        semantic_router=GeminiSemanticRouter(generate=transport),
    )
    got = _post(service, {"query": "물기 교육도 알려주고 걷는 것도 봐줘"}, _app_token())
    assert got.status_code == 200
    body = got.json()
    # 결정적 집계 진리표 (contracts §5): OK 단일 결과 → ANSWERED, handoffs 는 별도 보존.
    assert body["status"] == "ANSWERED"
    assert len(body["results"]) == 1
    assert body["handoffs"] == [{"target": "gait", "reason": "video_upload_required"}]
    assert training.calls  # Training 은 실행됐다
    # Gait 에는 애초에 어댑터가 없다 — 실행됐다면 그래프가 unsupported_capability ERROR 를
    # results 에 냈을 것이다. results 가 training 결과 하나뿐이라는 assert 가 이미 그것을 막는다.


# --------------------------------------------------------------------- 8. Router failure


def test_두_번째_스키마_실패도_아무것도_실행하지_않고_FAILED다() -> None:
    training = RecordingAdapter(
        CapabilityName.TRAINING, _ok(CapabilityName.TRAINING, "실행되면 안 됨")
    )
    transport = ScriptedTransport("this is not json", "{also not json")
    service = AssistantOrchestrationService(
        engine=OrchestrationEngine({CapabilityName.TRAINING: training}),
        semantic_router=GeminiSemanticRouter(generate=transport),
    )
    got = _post(service, {"query": QUERY}, _app_token())
    assert got.status_code == 200
    body = got.json()
    assert body["status"] == "FAILED"
    assert body["clarify"] is None
    assert body["results"] == [] and body["handoffs"] == []
    assert len(transport.prompts) == 2  # exactly one retry, O-14
    assert training.calls == []


# ------------------------------------------------------- 9. Semantic Walk without coords


def test_의미_경로로_선택된_walk도_좌표_없으면_CLARIFY_하나만_남는다() -> None:
    walk = RecordingAdapter(CapabilityName.WALK, _ok(CapabilityName.WALK, "실행되면 안 됨"))
    transport = ScriptedTransport(_decision(execute=["walk"]))
    service = AssistantOrchestrationService(
        engine=OrchestrationEngine({CapabilityName.WALK: walk}),
        semantic_router=GeminiSemanticRouter(generate=transport),
    )
    got = _post(service, {"query": "오늘 산책 나가도 괜찮을까?"}, _app_token())
    assert got.status_code == 200
    body = got.json()
    assert body["status"] == "CLARIFY"
    assert body["results"] == [] and body["handoffs"] == []
    assert walk.calls == []


# ------------------------------------------------------------------------- Auth boundary


def test_인증_없이는_오케스트레이션에_닿지_못한다() -> None:
    training = RecordingAdapter(
        CapabilityName.TRAINING, _ok(CapabilityName.TRAINING, "닿으면 안 됨")
    )
    transport = ScriptedTransport()
    service = AssistantOrchestrationService(
        engine=OrchestrationEngine({CapabilityName.TRAINING: training}),
        semantic_router=GeminiSemanticRouter(generate=transport),
    )
    got = _post(service, {"query": QUERY, "requested_capability": "training"}, token=None)
    assert got.status_code == 401
    assert training.calls == []
    assert transport.prompts == []
