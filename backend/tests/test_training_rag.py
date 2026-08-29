"""DAENGS Training RAG gateway의 adapter·HTTP 계약."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daengs_backend.core.deps import Principal
from daengs_backend.core.subject import SubjectType
from daengs_backend.core.token import create_access_token
from daengs_backend.routers.training import (
    get_training_rag_client,
    require_training_access,
    router,
)
from daengs_backend.schemas.training import TrainingChatResponse
from daengs_backend.services.training_rag import (
    TrainingRagClient,
    TrainingRagTimeoutError,
    TrainingRagUnavailableError,
)


def _upstream_response(*, decision: str = "ANSWER", reason: str = "") -> dict[str, object]:
    return {
        "request_id": "rag-request-1",
        "decision": decision,
        "reason": reason,
        "answer": "근거 기반 답변입니다.",
        "evidence": [
            {"rank": 1, "heading_path": ["반려동물", "예절교육"]},
            {"rank": 2, "heading_path": []},
        ],
    }


@pytest.mark.asyncio
async def test_adapter_maps_answer_and_hides_internal_fields() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/chat"
        assert request.headers["x-request-id"] == "trace-1"
        assert json.loads(request.content) == {"question": "배변 훈련을 시작하려면?", "top_k": 4}
        return httpx.Response(200, json=_upstream_response())

    client = TrainingRagClient(
        base_url="http://training-rag.test",
        connect_timeout_seconds=1,
        read_timeout_seconds=1,
        transport=httpx.MockTransport(handler),
    )

    response = await client.ask(question="배변 훈련을 시작하려면?", trace_id="trace-1")

    assert response.decision == "ANSWER"
    assert response.answer == "근거 기반 답변입니다."
    assert [citation.model_dump() for citation in response.citations] == [
        {"rank": 1, "label": "예절교육"},
        {"rank": 2, "label": "훈련 근거 2"},
    ]
    assert "score" not in response.model_dump_json()
    assert "chunk_id" not in response.model_dump_json()


async def _ask_refuse(reason: str) -> str:
    """상류 REFUSE 하나를 reason 별로 공개 판정에 옮긴다."""

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_upstream_response(decision="REFUSE", reason=reason))

    client = TrainingRagClient(
        base_url="http://training-rag.test",
        connect_timeout_seconds=1,
        read_timeout_seconds=1,
        transport=httpx.MockTransport(handler),
    )
    response = await client.ask(question="질문", trace_id="trace-refuse")
    return response.decision


@pytest.mark.asyncio
async def test_safety_boundary_refuse_is_not_shown_as_a_material_shortage() -> None:
    """체벌·임의 투약 거절이 `UNCERTAIN`("현재 자료 범위")으로 뜨면 안 된다.

    자료가 더 있으면 답해 준다는 뜻으로 읽히는데, 이 거절은 자료의 문제가 아니다.
    상류(`scripts/pgvector_runtime.gate`)가 두 경우에 다른 reason 을 준다.
    """
    assert await _ask_refuse("safety_boundary_training_harm") == "SAFETY_REFUSAL"


@pytest.mark.asyncio
async def test_medical_boundary_refuse_keeps_the_medical_label() -> None:
    assert await _ask_refuse("safety_boundary_medical") == "MEDICAL_REFUSAL"


@pytest.mark.asyncio
async def test_empty_retrieval_refuse_is_still_uncertain() -> None:
    """근거가 정말로 없을 때는 종전 그대로다."""
    assert await _ask_refuse("no_results") == "UNCERTAIN"


@pytest.mark.asyncio
async def test_adapter_normalizes_internal_refuse_to_uncertain() -> None:
    """reason 이 없는 상류(옛 버전)에서도 계약이 깨지지 않는다."""

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_upstream_response(decision="REFUSE"))

    client = TrainingRagClient(
        base_url="http://training-rag.test",
        connect_timeout_seconds=1,
        read_timeout_seconds=1,
        transport=httpx.MockTransport(handler),
    )

    response = await client.ask(question="지원 범위 밖 질문", trace_id="trace-2")

    assert response.decision == "UNCERTAIN"


@pytest.mark.asyncio
async def test_adapter_converts_upstream_500_to_unavailable() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "internal"})

    client = TrainingRagClient(
        base_url="http://training-rag.test",
        connect_timeout_seconds=1,
        read_timeout_seconds=1,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(TrainingRagUnavailableError):
        await client.ask(question="테스트", trace_id="trace-3")


class _FakeTrainingRagClient:
    def __init__(self, response: TrainingChatResponse | Exception) -> None:
        self.response = response

    async def ask(self, *, question: str, trace_id: str) -> TrainingChatResponse:
        assert question == "질문"
        assert uuid.UUID(trace_id)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _gateway_client(result: TrainingChatResponse | Exception) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_training_access] = lambda: Principal(uuid.uuid4(), "ADMIN")
    app.dependency_overrides[get_training_rag_client] = lambda: _FakeTrainingRagClient(result)
    return TestClient(app)


def _authenticated_client() -> TestClient:
    """**인증을 우회하지 않는** 클라이언트.

    위의 `_gateway_client` 는 `require_training_access` 를 통째로 갈아끼우므로
    인증 분기를 한 줄도 지나지 않는다. `#30` 이 그 분기를 앱 회원에서 관리자로
    바꿨으니, 그것만은 진짜 토큰을 만들어 확인한다.
    """
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_training_rag_client] = lambda: _FakeTrainingRagClient(
        TrainingChatResponse(decision="ANSWER", answer="answer", citations=[])
    )
    return TestClient(app)


def _post(client: TestClient, token: str | None = None):  # noqa: ANN202
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post("/training/chat", json={"question": "질문"}, headers=headers)


def test_gateway_requires_authentication_by_default() -> None:
    assert _post(_authenticated_client()).status_code == 401


def test_gateway_admits_admin_with_search_inspect() -> None:
    """`/training/chat` 은 **관리자 전용**이다 (`#30`).

    `#25` 가 랜딩 시연용으로 만든 임시 게이트웨이이고 앱 클라이언트가 없다.
    기준은 role 이 아니라 `Perm.SEARCH_INSPECT` — 콘솔의 `검색 점검` 메뉴와 같다.
    """
    token = create_access_token(uuid.uuid4(), SubjectType.ADMIN, "ADMIN")

    assert _post(_authenticated_client(), token).status_code == 200


def test_gateway_rejects_admin_without_search_inspect() -> None:
    """VIEWER 는 `READ` 뿐이라 못 들어온다. 401 이 아니라 403 인 것까지 고정한다 —
    401 을 주면 프론트(`lib/api.ts`)가 재발급하며 돈다."""
    token = create_access_token(uuid.uuid4(), SubjectType.ADMIN, "VIEWER")

    assert _post(_authenticated_client(), token).status_code == 403


def test_gateway_rejects_app_member() -> None:
    """`/walk` 과 달리 앱 회원은 못 부른다. 401 이다 — 재발급해도 토큰 종류는 안 바뀐다."""
    token = create_access_token(uuid.uuid4(), SubjectType.APP)

    assert _post(_authenticated_client(), token).status_code == 401


@pytest.mark.parametrize("decision", ["ANSWER", "UNCERTAIN", "SAFETY_REFUSAL", "MEDICAL_REFUSAL"])
def test_gateway_preserves_public_decisions(decision: str) -> None:
    client = _gateway_client(
        TrainingChatResponse(decision=decision, answer="안내", citations=[])
    )

    response = client.post("/training/chat", json={"question": "질문"})

    assert response.status_code == 200
    assert response.json() == {"decision": decision, "answer": "안내", "citations": []}
    assert uuid.UUID(response.headers["x-request-id"])


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (TrainingRagTimeoutError(), 504),
        (TrainingRagUnavailableError(), 503),
    ],
)
def test_gateway_converts_service_failures(error: Exception, expected_status: int) -> None:
    client = _gateway_client(error)

    response = client.post("/training/chat", json={"question": "질문"})

    assert response.status_code == expected_status
    assert "잠시 후 다시" in response.json()["detail"]
    assert uuid.UUID(response.headers["x-request-id"])
