"""DAENGS Training RAG gateway의 adapter·HTTP 계약."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daengs_backend.core.deps import AppPrincipal
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


def _upstream_response(*, decision: str = "ANSWER") -> dict[str, object]:
    return {
        "request_id": "rag-request-1",
        "decision": decision,
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


@pytest.mark.asyncio
async def test_adapter_normalizes_internal_refuse_to_uncertain() -> None:
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
    app.dependency_overrides[require_training_access] = lambda: AppPrincipal(uuid.uuid4())
    app.dependency_overrides[get_training_rag_client] = lambda: _FakeTrainingRagClient(result)
    return TestClient(app)


def test_gateway_requires_existing_app_access_by_default() -> None:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_training_rag_client] = lambda: _FakeTrainingRagClient(
        TrainingChatResponse(decision="ANSWER", answer="answer", citations=[])
    )

    response = TestClient(app).post("/training/chat", json={"question": "질문"})

    assert response.status_code == 401


@pytest.mark.parametrize("decision", ["ANSWER", "UNCERTAIN", "MEDICAL_REFUSAL"])
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
