"""`POST /assistant/query` HTTP 경계 (Card 3).

여기서 보는 것은 문(門)과 DTO 검증뿐이다 — 의미 라우팅·결정론적 조립·능력 실행
자체의 의미론은 `test_orchestration_semantic_router.py` 가 이미 본다. 그 테스트를
다시 하지 않는다: `AssistantOrchestrationService` 는 (몇몇 예외를 빼고) 표식으로
갈아끼운다 — `test_ask_auth.py` 가 `service.ask` 를 표식으로 막는 것과 같은 자리다.

`admin_or_app_user(Perm.READ)` 를 실제 토큰으로 통과시킨다(오버라이드하지 않는다) —
`test_ask_auth.py`·`/life/walk-conditions` 가 검증한 문을 이 카드가 다시 배선하지 않았다는 것 자체가
검증 대상이기 때문이다.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from daengs_backend.core.subject import SubjectType
from daengs_backend.core.token import create_access_token
from daengs_backend.orchestration.contracts import (
    AssistantResponse,
    AssistantStatus,
    CapabilityName,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    PrincipalContext,
)
from daengs_backend.orchestration.graph import OrchestrationEngine
from daengs_backend.orchestration.semantic import GeminiSemanticRouter
from daengs_backend.orchestration.service import AssistantOrchestrationService
from daengs_backend.routers import assistant as assistant_router

QUERY = "우리 개가 산책 중에 짖어요. 어떻게 교육하죠?"


class Reached(Exception):
    """서비스 경계까지 왔다는 표시."""


class FakeAssistantOrchestrationService:
    """`AssistantOrchestrationService.run()` 서명만 흉내 낸다. 계획/실행은 하지 않는다."""

    def __init__(self, response: AssistantResponse | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._response = response or _answered_response()

    async def run(
        self,
        *,
        query: str,
        principal: PrincipalContext,
        context: dict[str, Any] | None = None,
        requested_capability: str | None = None,
        request_id: str | None = None,
        locale: str = "ko-KR",
    ) -> AssistantResponse:
        self.calls.append(
            {
                "query": query,
                "principal": principal,
                "context": context,
                "requested_capability": requested_capability,
            }
        )
        return self._response


def _answered_response() -> AssistantResponse:
    return AssistantResponse(
        request_id="test-request",
        status=AssistantStatus.ANSWERED,
        message="답변입니다",
        results=[],
        handoffs=[],
        clarify=None,
    )


@pytest.fixture
def fake_service() -> FakeAssistantOrchestrationService:
    return FakeAssistantOrchestrationService()


@pytest.fixture
def client(fake_service: FakeAssistantOrchestrationService) -> Iterator[TestClient]:
    """실제 앱, 실제 인증. 서비스만 표식으로 갈아끼운다."""
    from daengs_backend.main import app

    app.dependency_overrides[assistant_router.get_assistant_orchestration_service] = lambda: (
        fake_service
    )
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def _app_token() -> str:
    return create_access_token(uuid.uuid4(), SubjectType.APP)


def _admin_token(role: str = "ADMIN") -> str:
    return create_access_token(uuid.uuid4(), SubjectType.ADMIN, role)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _post(client: TestClient, body: dict[str, Any], token: str | None = None) -> Any:
    headers = _auth(token) if token else {}
    return client.post("/assistant/query", json=body, headers=headers)


# --------------------------------------------------------------------- 인증


def test_앱_회원_토큰이면_200이고_서비스가_한_번_불린다(
    client: TestClient, fake_service: FakeAssistantOrchestrationService
) -> None:
    got = _post(client, {"query": QUERY}, _app_token())
    assert got.status_code == 200
    assert len(fake_service.calls) == 1


def test_관리자_토큰이면_200이고_서비스가_한_번_불린다(
    client: TestClient, fake_service: FakeAssistantOrchestrationService
) -> None:
    got = _post(client, {"query": QUERY}, _admin_token())
    assert got.status_code == 200
    assert len(fake_service.calls) == 1


def test_토큰_없이_부르면_401(
    client: TestClient, fake_service: FakeAssistantOrchestrationService
) -> None:
    got = _post(client, {"query": QUERY})
    assert got.status_code == 401
    assert fake_service.calls == []


def test_모르는_role이면_403(
    client: TestClient, fake_service: FakeAssistantOrchestrationService
) -> None:
    """`test_ask_auth.py` 의 같은 케이스와 동일한 판단 — READ 는 전 role 이 갖지만,
    role 자체가 `ROLE_PERMISSIONS` 에 없으면 권한이 빈 집합이라 403 이다."""
    got = _post(client, {"query": QUERY}, _admin_token(role="NOT_A_ROLE"))
    assert got.status_code == 403
    assert fake_service.calls == []


def test_인증이_본문_검증보다_먼저다(
    client: TestClient, fake_service: FakeAssistantOrchestrationService
) -> None:
    """토큰 없음 + 빈 질의 → 401 이지 422 가 아니다 (`test_ask_auth.py` 와 같은 자리)."""
    got = _post(client, {"query": ""})
    assert got.status_code == 401
    assert fake_service.calls == []


# ------------------------------------------------------------------- query


@pytest.mark.parametrize("query", ["", "   ", "\t\n"], ids=["empty", "spaces", "tab_newline"])
def test_빈_질의는_422이고_서비스는_안_불린다(
    client: TestClient, fake_service: FakeAssistantOrchestrationService, query: str
) -> None:
    got = _post(client, {"query": query}, _app_token())
    assert got.status_code == 422
    assert fake_service.calls == []


def test_질의_원문은_공백을_포함해_그대로_전달된다(
    client: TestClient, fake_service: FakeAssistantOrchestrationService
) -> None:
    """검증은 `.strip()` 으로 하되, 실제로 넘기는 값은 원문 그대로다 — orchestration 이
    원문 보존의 소유자다 (semantic.py 의 정확한 원문 요구와 같은 자리)."""
    padded = f"  {QUERY}  "
    got = _post(client, {"query": padded}, _app_token())
    assert got.status_code == 200
    assert fake_service.calls[0]["query"] == padded


def test_place_내부_계약보다_긴_질의는_HTTP_경계에서_422다(
    client: TestClient, fake_service: FakeAssistantOrchestrationService
) -> None:
    got = _post(
        client,
        {"query": "가" * 1_001, "requested_capability": "place"},
        _app_token(),
    )
    assert got.status_code == 422
    assert fake_service.calls == []


# --------------------------------------------------------- 라우팅 메타데이터


def test_유효한_메타데이터는_그대로_컨텍스트로_전달된다(
    client: TestClient, fake_service: FakeAssistantOrchestrationService
) -> None:
    got = _post(
        client,
        {
            "query": QUERY,
            "source": "assistant",
            "action": "chat_send",
            "active_dog_id": "dog-1",
        },
        _app_token(),
    )
    assert got.status_code == 200
    assert fake_service.calls[0]["context"] == {
        "source": "assistant",
        "action": "chat_send",
        "active_dog_id": "dog-1",
    }


@pytest.mark.parametrize("field", ["source", "action", "active_dog_id"])
@pytest.mark.parametrize("value", ["", "   "], ids=["empty", "spaces"])
def test_빈_메타데이터는_422이고_서비스는_안_불린다(
    client: TestClient,
    fake_service: FakeAssistantOrchestrationService,
    field: str,
    value: str,
) -> None:
    got = _post(client, {"query": QUERY, field: value}, _app_token())
    assert got.status_code == 422
    assert fake_service.calls == []


@pytest.mark.parametrize("field", ["source", "action", "active_dog_id"])
@pytest.mark.parametrize(
    "value", [{"nested": "dict"}, ["a", "list"], 123, True], ids=["dict", "list", "number", "bool"]
)
def test_모양이_틀린_메타데이터는_422이고_서비스는_안_불린다(
    client: TestClient,
    fake_service: FakeAssistantOrchestrationService,
    field: str,
    value: object,
) -> None:
    """Card 2B 내부 fail-fast(`build_semantic_router_prompt` 의 `ValueError`)에
    닿기 전에 이 경계가 막아야 하는 바로 그 모양들이다."""
    got = _post(client, {"query": QUERY, field: value}, _app_token())
    assert got.status_code == 422
    assert fake_service.calls == []


# --------------------------------------------------------------- location


def test_유효한_위치는_그대로_전달된다(
    client: TestClient, fake_service: FakeAssistantOrchestrationService
) -> None:
    got = _post(
        client,
        {"query": QUERY, "location": {"lat": 37.5665, "lon": 126.978}},
        _app_token(),
    )
    assert got.status_code == 200
    assert fake_service.calls[0]["context"] == {"location": {"lat": 37.5665, "lon": 126.978}}


def test_위치가_없으면_유효한_요청이고_서비스가_불린다(
    client: TestClient, fake_service: FakeAssistantOrchestrationService
) -> None:
    """CLARIFY 는 planner 의 것이지 HTTP 검증의 것이 아니다 — Walk 가 나중에 선택되면
    그때 기존 결정론적 planner 가 CLARIFY 를 낸다."""
    got = _post(client, {"query": QUERY}, _app_token())
    assert got.status_code == 200
    assert len(fake_service.calls) == 1
    assert "location" not in fake_service.calls[0]["context"]


@pytest.mark.parametrize(
    "location",
    [
        {"lat": 10.0, "lon": 126.978},
        {"lat": 37.5665, "lon": 10.0},
        {"lat": "북위", "lon": 126.978},
        {"lat": 37.5665},
        {"lat": 37.5665, "lon": 126.978, "alt": 10},
    ],
    ids=["lat_out_of_range", "lon_out_of_range", "lat_not_numeric", "missing_lon", "extra_field"],
)
def test_잘못된_위치는_422이고_서비스는_안_불린다(
    client: TestClient, fake_service: FakeAssistantOrchestrationService, location: dict
) -> None:
    got = _post(client, {"query": QUERY, "location": location}, _app_token())
    assert got.status_code == 422
    assert fake_service.calls == []


# ------------------------------------------------------------- DTO 경계


def test_최상위_추가_필드는_422(
    client: TestClient, fake_service: FakeAssistantOrchestrationService
) -> None:
    got = _post(client, {"query": QUERY, "raw_context": {"anything": "goes"}}, _app_token())
    assert got.status_code == 422
    assert fake_service.calls == []


@pytest.mark.parametrize(
    "field", ["token", "authorization", "credentials", "user", "principal", "permissions"]
)
def test_인증정보처럼_생긴_추가_필드는_422이고_절대_서비스로_가지_않는다(
    client: TestClient, fake_service: FakeAssistantOrchestrationService, field: str
) -> None:
    got = _post(client, {"query": QUERY, field: "should-never-pass"}, _app_token())
    assert got.status_code == 422
    assert fake_service.calls == []


# --------------------------------------------------- requested_capability


def test_requested_capability는_그대로_전달되고_인가에_관여하지_않는다(
    client: TestClient, fake_service: FakeAssistantOrchestrationService
) -> None:
    got = _post(client, {"query": QUERY, "requested_capability": "training"}, _app_token())
    assert got.status_code == 200
    call = fake_service.calls[0]
    assert call["requested_capability"] == "training"
    # 라우팅 신호일 뿐이다 — PrincipalContext 의 신원/권한과는 무관하다 (D-036).
    assert call["principal"].kind == "APP_USER"


def test_place_requested_capability도_인가와_분리된_신호로_전달된다(
    client: TestClient, fake_service: FakeAssistantOrchestrationService
) -> None:
    """HTTP DTO는 enum을 소유하지 않고 planner가 명시 Place 경로를 해석한다."""
    got = _post(client, {"query": QUERY, "requested_capability": "place"}, _app_token())
    assert got.status_code == 200
    assert fake_service.calls[0]["requested_capability"] == "place"


def test_지원하지_않는_requested_capability도_422가_아니라_그대로_넘어간다(
    client: TestClient, fake_service: FakeAssistantOrchestrationService
) -> None:
    got = _post(client, {"query": QUERY, "requested_capability": "calendar"}, _app_token())
    assert got.status_code == 200
    assert fake_service.calls[0]["requested_capability"] == "calendar"


# ------------------------------------------------------------ Principal


def test_앱_회원_PrincipalContext는_인증된_신원만_담는다(
    client: TestClient, fake_service: FakeAssistantOrchestrationService
) -> None:
    got = _post(client, {"query": QUERY}, _app_token())
    assert got.status_code == 200
    principal = fake_service.calls[0]["principal"]
    assert principal.kind == "APP_USER"
    assert principal.permissions == ()
    uuid.UUID(principal.subject)  # 토큰의 subject 그대로인 UUID 문자열


def test_관리자_PrincipalContext는_인증된_신원과_권한만_담는다(
    client: TestClient, fake_service: FakeAssistantOrchestrationService
) -> None:
    got = _post(client, {"query": QUERY}, _admin_token(role="VIEWER"))
    assert got.status_code == 200
    principal = fake_service.calls[0]["principal"]
    assert principal.kind == "ADMIN"
    assert principal.permissions == ("read",)
    uuid.UUID(principal.subject)


# -------------------------------------------------------------- 응답


def test_응답은_AssistantResponse_그대로다(client: TestClient) -> None:
    """FAILED 를 포함해 상태를 재해석하지 않는다 (docs/orchestration/contracts.md §5)."""
    failed = AssistantResponse(
        request_id="req-failed",
        status=AssistantStatus.FAILED,
        message="요청을 해석하지 못했습니다. 잠시 후 다시 시도해 주세요.",
        results=[],
        handoffs=[],
        clarify=None,
    )
    fake = FakeAssistantOrchestrationService(response=failed)
    from daengs_backend.main import app

    app.dependency_overrides[assistant_router.get_assistant_orchestration_service] = lambda: fake
    try:
        with TestClient(app) as c:
            got = c.post("/assistant/query", json={"query": QUERY}, headers=_auth(_app_token()))
    finally:
        app.dependency_overrides.clear()

    assert got.status_code == 200
    body = got.json()
    assert body["status"] == "FAILED"
    assert body["request_id"] == "req-failed"


# ------------------------------------------------------- 통합 스모크


def test_실제_서비스_경계를_통해_끝까지_간다() -> None:
    """가짜 서비스가 아니라 실제 `AssistantOrchestrationService` — Gemini 전송과
    Card 1 어댑터만 스크립트로 대체한다. HTTP → 인증 → 서비스 → 직렬화까지 한 번에 본다.
    라이브 provider 는 부르지 않는다 — Card 2B 스위트가 이미 그 의미론을 본다.
    """
    import json as _json

    class ScriptedTransport:
        async def __call__(self, _prompt: str) -> str:
            return _json.dumps({"execute": ["training"], "handoffs": []})

    class FakeTrainingAdapter:
        capability = CapabilityName.TRAINING

        async def run(self, request: CapabilityRequest, *, request_id: str) -> CapabilityResult:
            assert request.payload.question == QUERY
            return CapabilityResult(
                capability=self.capability,
                status=CapabilityStatus.OK,
                data={"answer": "짖음 교육 방법입니다"},
                elapsed_ms=1,
            )

    real_service = AssistantOrchestrationService(
        engine=OrchestrationEngine({CapabilityName.TRAINING: FakeTrainingAdapter()}),
        semantic_router=GeminiSemanticRouter(generate=ScriptedTransport()),
    )
    from daengs_backend.main import app

    app.dependency_overrides[assistant_router.get_assistant_orchestration_service] = lambda: (
        real_service
    )
    try:
        with TestClient(app) as c:
            got = c.post("/assistant/query", json={"query": QUERY}, headers=_auth(_app_token()))
    finally:
        app.dependency_overrides.clear()

    assert got.status_code == 200
    body = got.json()
    assert body["status"] == "ANSWERED"
    assert body["results"][0]["data"]["answer"] == "짖음 교육 방법입니다"
