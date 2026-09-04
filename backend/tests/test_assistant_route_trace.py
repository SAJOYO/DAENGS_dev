"""`AssistantResponse.route` — 어느 길로 갔나를 누가 보는가 (#238).

**점검 권한(`search:inspect`)이 있을 때만 실린다.** `/assistant/query` 는 앱 회원도 부르는
공개 경로라, 라우터 종류와 모델 이름을 그냥 넣으면 앱 사용자에게도 가고 저장되는 대화 turn
에도 남는다(`services/chat.py public_response_of` 가 응답 전체를 적재한다). 그래서 이 파일이
지키는 것은 값이 **맞게 나오는가** 와 값이 **엉뚱한 사람에게 안 가는가** 둘이다.

실제 앱·실제 인증으로 부르고, Gemini 는 가짜 transport 라 과금이 없다. 능력 어댑터도 가짜다 —
여기서 보는 것은 답의 내용이 아니라 답이 나온 경위다.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

import pytest
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
from daengs_backend.orchestration.semantic import (
    PROMPT_VERSION,
    ROUTER_MODEL_ID,
    GeminiSemanticRouter,
    SemanticRoutingError,
)
from daengs_backend.orchestration.service import AssistantOrchestrationService
from daengs_backend.routers import assistant as assistant_router

QUERY = "우리 개가 산책 중에 짖어요. 어떻게 교육하죠?"
#: 앱이 쓰던 응답의 필드. `route` 는 여기 더해진 일곱 번째이고, 이 여섯은 그대로다.
PUBLIC_FIELDS = {"request_id", "status", "message", "results", "handoffs", "clarify"}


class ScriptedTransport:
    """가짜 Gemini. 넣어 둔 것을 순서대로 돌려주고, 받은 프롬프트를 들고 있는다."""

    def __init__(self, *outputs: object) -> None:
        self.outputs = list(outputs)
        self.prompts: list[str] = []

    async def __call__(self, prompt: str) -> object:
        self.prompts.append(prompt)
        result = self.outputs.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeAdapter:
    def __init__(self, capability: CapabilityName) -> None:
        self.capability = capability

    async def run(self, request: CapabilityRequest, *, request_id: str) -> CapabilityResult:
        return CapabilityResult(
            capability=self.capability,
            status=CapabilityStatus.OK,
            data={"answer": f"{self.capability.value} answer"},
            elapsed_ms=1,
        )


def decision(execute: list[str] = (), social_intent: str | None = None) -> str:
    return json.dumps({"execute": list(execute), "handoffs": [], "social_intent": social_intent})


def build_service(*outputs: object) -> tuple[AssistantOrchestrationService, ScriptedTransport]:
    transport = ScriptedTransport(*outputs)
    engine = OrchestrationEngine({name: FakeAdapter(name) for name in CapabilityName})
    service = AssistantOrchestrationService(
        engine=engine, semantic_router=GeminiSemanticRouter(generate=transport)
    )
    return service, transport


def _app_token() -> str:
    return create_access_token(uuid.uuid4(), SubjectType.APP)


def _admin_token(role: str) -> str:
    return create_access_token(uuid.uuid4(), SubjectType.ADMIN, role)


def _post(service: AssistantOrchestrationService, body: dict[str, Any], token: str) -> Any:
    from daengs_backend.main import app

    app.dependency_overrides[assistant_router.get_assistant_orchestration_service] = (
        lambda: service
    )
    try:
        with TestClient(app) as client:
            return client.post(
                "/assistant/query", json=body, headers={"Authorization": f"Bearer {token}"}
            )
    finally:
        app.dependency_overrides.clear()


# ------------------------------------------------------------------ 누가 보는가


@pytest.mark.parametrize(
    ("who", "token"),
    [
        ("앱 회원", _app_token),
        ("VIEWER 관리자", lambda: _admin_token("VIEWER")),
    ],
)
def test_점검_권한이_없으면_라우팅_메타데이터가_가지_않는다(
    who: str, token: Callable[[], str]
) -> None:
    """앱 회원은 권한 목록 자체가 비어 있고(`_principal_context`), VIEWER 는 read 뿐이다."""
    service, _ = build_service()
    got = _post(service, {"query": QUERY, "requested_capability": "life"}, token())
    assert got.status_code == 200
    assert got.json()["route"] is None, who


def test_앱이_쓰던_응답은_모양이_그대로다() -> None:
    """`route` 가 더해진 것 말고 앱이 읽던 여섯 필드는 하나도 안 바뀐다."""
    service, _ = build_service()
    got = _post(service, {"query": QUERY, "requested_capability": "life"}, _app_token())
    body = got.json()
    assert set(body) == PUBLIC_FIELDS | {"route"}
    assert body["status"] == "ANSWERED"
    assert body["message"] == "life answer"
    assert [item["capability"] for item in body["results"]] == ["life"]
    assert body["handoffs"] == [] and body["clarify"] is None


def test_점검은_role_이_아니라_권한으로_갈린다() -> None:
    """ANALYST 는 쓰기 권한이 없지만 `search:inspect` 는 가진다 — 그것으로 충분하다."""
    service, _ = build_service()
    got = _post(service, {"query": QUERY, "requested_capability": "life"}, _admin_token("ANALYST"))
    assert got.json()["route"] == {
        "router": "deterministic",
        "model": None,
        "prompt_version": None,
    }


# --------------------------------------------------------------- 무엇이 보이는가


def test_결정적_경로는_부른_모델이_없다() -> None:
    """`model`·`prompt_version` 이 None 인 것은 모르는 것이 아니라 **없는 것**이다."""
    service, transport = build_service()
    got = _post(
        service, {"query": QUERY, "requested_capability": "training"}, _admin_token("ADMIN")
    )
    assert got.json()["route"] == {
        "router": "deterministic",
        "model": None,
        "prompt_version": None,
    }
    assert transport.prompts == []  # 모델을 한 번도 안 불렀다


def test_의미_라우팅은_모델과_프롬프트_버전을_싣는다() -> None:
    service, transport = build_service(decision(["training"]))
    got = _post(service, {"query": QUERY}, _admin_token("ADMIN"))
    assert got.json()["route"] == {
        "router": "llm",
        "model": ROUTER_MODEL_ID,
        "prompt_version": PROMPT_VERSION,
    }
    assert len(transport.prompts) == 1


def test_되물음에도_어느_길로_갔는지_보인다() -> None:
    """CLARIFY 는 배타적이라 실행된 능력이 없다 — 그래서 더 경위가 필요하다 (O-8)."""
    service, _ = build_service(decision(["walk"]))
    got = _post(service, {"query": "지금 산책 나가도 될까?"}, _admin_token("ADMIN"))
    body = got.json()
    assert body["status"] == "CLARIFY" and body["results"] == []
    assert body["route"]["router"] == "llm"


def test_인사말도_어느_길로_갔는지_보인다() -> None:
    """사교 발화는 RoutePlan 없이 고정 문구로 답한다. 화면에서는 "왜 아무것도 안 돌았지" 로
    보이는 자리라, 라우터가 그렇게 분류했다는 것이 유일한 설명이다."""
    service, _ = build_service(decision(social_intent="greeting"))
    got = _post(service, {"query": "안녕하세요"}, _admin_token("ADMIN"))
    body = got.json()
    assert body["status"] == "ANSWERED" and body["results"] == []
    assert body["route"] == {
        "router": "llm",
        "model": ROUTER_MODEL_ID,
        "prompt_version": PROMPT_VERSION,
    }


def test_라우터가_실패해도_어느_길로_갔는지_보인다() -> None:
    """라우터 실패는 CLARIFY 가 아니라 FAILED 이고(불변식 10), 모델 원출력은 안 나간다."""
    service, _ = build_service(SemanticRoutingError("boom"))
    got = _post(service, {"query": QUERY}, _admin_token("ADMIN"))
    body = got.json()
    assert body["status"] == "FAILED" and body["results"] == []
    assert body["route"]["router"] == "llm"
    assert "boom" not in json.dumps(body, ensure_ascii=False)


def test_앱_회원에게는_사교_응답에도_안_실린다() -> None:
    service, _ = build_service(decision(social_intent="greeting"))
    got = _post(service, {"query": "안녕하세요"}, _app_token())
    assert got.json()["route"] is None


# --------------------------------------------------------------------- D-037


def test_라우팅_메타데이터에_질문_원문이_섞이지_않는다() -> None:
    """내는 것은 메타데이터지 원문이 아니다 (D-037). 프롬프트 본문도 마찬가지다."""
    marker = "우리 개 이름은 마루이고 주소는 서울시 강남구입니다"
    service, transport = build_service(decision(["life"]))
    got = _post(service, {"query": marker}, _admin_token("ADMIN"))
    route = got.json()["route"]
    assert set(route) == {"router", "model", "prompt_version"}
    assert marker not in json.dumps(route, ensure_ascii=False)
    assert transport.prompts[0] not in json.dumps(route, ensure_ascii=False)
