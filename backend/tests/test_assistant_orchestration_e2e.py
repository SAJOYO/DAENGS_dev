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

import httpx
from fastapi.testclient import TestClient

from daengs_backend.core.subject import SubjectType
from daengs_backend.core.token import create_access_token
from daengs_backend.orchestration.adapters.place import PlaceCapabilityAdapter
from daengs_backend.orchestration.contracts import (
    CapabilityName,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
)
from daengs_backend.orchestration.graph import OrchestrationEngine
from daengs_backend.orchestration.redirects import NO_CAPABILITY_MESSAGE
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


# ---------------------------------------------------- 2-b. Life 경계 신호 (RAG-055 · #177)
# 여기서는 **진짜 `LifeCapabilityAdapter`** 를 쓴다 — 위 둘이 쓰는 `RecordingAdapter` 는 결과를
# 그대로 돌려주므로 어댑터의 번역을 건너뛴다. 이 카드가 지켜야 할 것이 바로 그 번역이라,
# 가짜로 두는 경계를 한 칸 안쪽(`services.ask` 가 던지는 HTTPException)으로 옮긴다.


def _life_adapter_raising(exc: Exception):
    from daengs_backend.orchestration.adapters.life import LifeCapabilityAdapter

    def ask(_: str, **_kw):
        raise exc

    return LifeCapabilityAdapter(ask)


def test_life_medical_boundary_reaches_the_user_as_refused() -> None:
    """증상 질문 → 최상위 `REFUSED` + `refusal.code` + Life 가 쓴 문장 그대로 (RAG-055 ⑤).

    이 사슬 전체가 진짜다 — HTTP · 인증 · 라우팅 · 그래프 · `aggregate_results`. 가짜는
    `services/ask.py` 가 던지는 예외 하나뿐이고, 그것이 이 카드가 새로 만든 계약이다.
    """
    from fastapi import HTTPException

    said = "반려동물의 증상에 대한 판단은 수의사의 진료를 통해 확인해야 합니다."
    life = _life_adapter_raising(
        HTTPException(status_code=422,
                      detail={"code": "medical_boundary", "message": said}))
    service = AssistantOrchestrationService(
        engine=OrchestrationEngine({CapabilityName.LIFE: life}),
        semantic_router=GeminiSemanticRouter(generate=ScriptedTransport()),
    )
    got = _post(service, {"query": "뒷다리를 절뚝거리는데 무슨 병인가요?",
                          "requested_capability": "life"}, _app_token())

    assert got.status_code == 200          # 거절은 실패가 아니다 — 능력이 낸 결과다
    body = got.json()
    assert body["status"] == "REFUSED"
    result = body["results"][0]
    assert result["status"] == "REFUSED"
    assert result["refusal"]["code"] == "medical_boundary"
    assert result["refusal"]["message"] == said     # 불변식 3 — 어댑터는 상태만 옮긴다


def test_life_weak_evidence_reaches_the_user_as_uncertain() -> None:
    """A0 의 목줄 질문이 가야 할 곳 — 최상위 `UNCERTAIN` (ABSTAINED, RAG-055 ⑤).

    옛 서빙은 이것을 `OK`/`ANSWERED` 로 내보냈고(A0 §3-1), 앱은 무관한 과태료를 나열한 답을
    정상 답변으로 보여 줬다. 코드가 `no_evidence` 그대로인 것은 기권의 **종류가 는 것**이지
    뜻이 바뀐 게 아니어서다.
    """
    from fastapi import HTTPException

    said = "제공해주신 자료에는 목줄 미착용에 대한 과태료 규정이 포함되어 있지 않습니다."
    life = _life_adapter_raising(
        HTTPException(status_code=404, detail={"code": "no_evidence", "message": said}))
    service = AssistantOrchestrationService(
        engine=OrchestrationEngine({CapabilityName.LIFE: life}),
        semantic_router=GeminiSemanticRouter(generate=ScriptedTransport()),
    )
    got = _post(service, {"query": "목줄 안 하면 과태료 얼마야",
                          "requested_capability": "life"}, _app_token())

    assert got.status_code == 200
    body = got.json()
    assert body["status"] == "UNCERTAIN"
    assert body["results"][0]["abstention"]["code"] == "no_evidence"
    assert body["results"][0]["abstention"]["message"] == said


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
    # message 는 사람이 읽을 문장이지, 라우팅 내부 값(target/reason)이 아니다.
    assert "skin" not in body["message"]
    assert "image_upload_required" not in body["message"]


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
    assert "gait" not in body["message"]
    assert "video_upload_required" not in body["message"]


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
    # 실행 답변은 남고, handoff 안내는 내부 코드 없이 이어붙는다.
    assert "훈련 답변" in body["message"]
    assert "gait" not in body["message"]
    assert "video_upload_required" not in body["message"]
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


# ------------------------------------------------------------ 8-b. Empty selection (#278)


def test_빈_선택은_스코프드_리다이렉트_문구로_FAILED다() -> None:
    """라우터가 아무것도 못 고르면(기본 플래그) 라우터 실패와는 다른 문구가 나간다.

    스키마 실패(위 8번)는 `_ROUTER_FAILURE_MESSAGE` — "요청을 해석하지 못했습니다".
    빈 선택은 요청은 해석됐지만 도울 능력이 없었던 것이라, "무엇은 도울 수 있다" 를
    말하는 스코프드 리다이렉트를 쓴다.
    """
    training = RecordingAdapter(
        CapabilityName.TRAINING, _ok(CapabilityName.TRAINING, "실행되면 안 됨")
    )
    transport = ScriptedTransport(_decision())
    service = AssistantOrchestrationService(
        engine=OrchestrationEngine({CapabilityName.TRAINING: training}),
        semantic_router=GeminiSemanticRouter(generate=transport),
    )
    got = _post(service, {"query": "비트코인 시세가 어떻게 돼?"}, _app_token())
    assert got.status_code == 200
    body = got.json()
    assert body["status"] == "FAILED"
    assert body["message"] == NO_CAPABILITY_MESSAGE
    assert body["results"] == [] and body["handoffs"] == []
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


# ------------------------------------------ Place targeted vertical HTTP slice


async def test_place_명시_신호가_실제_HTTP_adapter와_축약_projection까지_도달한다() -> None:
    """Everything except the place-search transport is production code.

    In particular, this crosses the public FastAPI DTO/auth boundary, deterministic planner,
    LangGraph, the real Place HTTP adapter, compact projection, and aggregate message.
    """

    internal = {
        "contract_version": "place-discovery-v1",
        "planning": {
            "contract_version": "place-discovery-planning-v1",
            "status": "ready",
            "source_disposition": "proposed",
            "resolution": "inferred",
            "lenses": {
                "target_lenses": [
                    {
                        "lens_id": "target:pet-shop",
                        "display_label": "#펫샵",
                        "mapping_scope": "direct",
                        "availability": "executable",
                        "support_note": "강아지 용품 구매 장소로 해석했어요.",
                    }
                ],
                "signal_lenses": [],
            },
            "issues": [],
        },
        "lens_results": [
            {
                "lens_id": "target:pet-shop",
                "display_label": "#펫샵",
                "support_note": "강아지 용품 구매 장소로 해석했어요.",
                "search": {
                    "groups": [
                        {
                            "results": [
                                {
                                    "place": {
                                        "key": {"source": "kcisa", "ref": "P-1"},
                                        "lat": 37.557,
                                        "lng": 126.924,
                                    }
                                }
                            ]
                        }
                    ]
                },
                "presentations": [
                    {
                        "place_key": {"source": "kcisa", "ref": "P-1"},
                        "title": "홍대 반려동물 용품점",
                        "summary": "강아지 용품 정보를 확인할 수 있어요.",
                        "kind_id": "pet_shop",
                        "kind_label": "펫샵",
                        "distance_m": 180,
                        "address": "서울 마포구",
                        "core_items": [],
                        "promoted_items": [],
                        "detail_items": [],
                        "notices": [],
                        "why_matched": [],
                    }
                ],
            }
        ],
        "notices": [],
    }
    seen: list[httpx.Request] = []

    async def place_search(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=internal)

    place_http = httpx.AsyncClient(transport=httpx.MockTransport(place_search))
    transport = ScriptedTransport()
    adapter = PlaceCapabilityAdapter(client=place_http, base_url="http://place-search:8000")
    service = AssistantOrchestrationService(
        engine=OrchestrationEngine({CapabilityName.PLACE: adapter}),
        semantic_router=GeminiSemanticRouter(generate=transport),
    )
    from daengs_backend.main import app

    app.dependency_overrides[assistant_router.get_assistant_orchestration_service] = lambda: service
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            got = await client.post(
                "/assistant/query",
                json={
                    "query": "강아지 장난감 사고 싶어",
                    "requested_capability": "place",
                    "location": {"lat": 37.5563, "lon": 126.9236},
                },
                headers={"Authorization": f"Bearer {_app_token()}"},
            )
    finally:
        app.dependency_overrides.clear()
        await place_http.aclose()

    assert got.status_code == 200
    body = got.json()
    assert body["status"] == "ANSWERED"
    assert body["results"][0]["capability"] == "place"
    assert body["results"][0]["data"]["contract_version"] == "place-capability-v1"
    candidate = body["results"][0]["data"]["groups"][0]["candidates"][0]
    assert candidate["place_id"] == {"source": "kcisa", "ref": "P-1"}
    assert candidate["location"] == {"lat": 37.557, "lon": 126.924, "distance_m": 180}
    assert len(seen) == 1
    assert transport.prompts == []
