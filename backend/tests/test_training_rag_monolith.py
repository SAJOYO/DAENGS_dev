"""Public Training API and local modular-monolith boundary tests."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daengs_backend.core.deps import Principal
from daengs_backend.routers.training import get_training_rag_service, require_training_access, router
from daengs_backend.services import training_rag
from daengs_backend.services.training_rag import TrainingRagService, TrainingRagUnavailableError
from daengs_training.service import ChatResponse, EvidenceCard


@pytest.fixture(autouse=True)
def restore_runtime_factory():
    original = training_rag.get_training_runtime
    yield
    training_rag.get_training_runtime = original


def upstream(decision: str = "ANSWER", reason: str = "grounded_generation") -> ChatResponse:
    return ChatResponse(
        request_id="runtime-request",
        answer="근거 기반 답변입니다.",
        decision=decision,
        reason=reason,
        generated=decision == "ANSWER",
        model=None,
        prompt_version=None,
        evidence=[EvidenceCard(rank=1, chunk_id="c1", document_id="d1", chunk_index=0,
                               heading_path=["훈련", "기초"], score=0.9)],
        gate={},
        usage=None,
        output_guardrail_blocked=False,
    )


class FakeRuntime:
    def __init__(self, result: ChatResponse | Exception) -> None:
        self.result = result
        self.thread_id: int | None = None

    def answer(self, question: str, top_k: int = 4) -> ChatResponse:
        assert question == "질문"
        assert top_k == 4
        self.thread_id = threading.get_ident()
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def client_for(result: ChatResponse | Exception) -> tuple[TestClient, FakeRuntime]:
    runtime = FakeRuntime(result)
    service = TrainingRagService()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_training_access] = lambda: Principal(uuid.uuid4(), "ADMIN")
    app.dependency_overrides[get_training_rag_service] = lambda: service
    training_rag.get_training_runtime = lambda: runtime  # type: ignore[method-assign]
    return TestClient(app), runtime


@pytest.mark.parametrize(
    ("internal", "reason", "public"),
    [
        ("ANSWER", "grounded_generation", "ANSWER"),
        ("UNCERTAIN", "low_score", "UNCERTAIN"),
        ("MEDICAL_REFUSAL", "medical_input_guardrail", "MEDICAL_REFUSAL"),
        ("REFUSE", "safety_boundary_training_harm", "SAFETY_REFUSAL"),
        ("REFUSE", "safety_boundary_medical", "MEDICAL_REFUSAL"),
        ("REFUSE", "no_results", "UNCERTAIN"),
    ],
)
def test_public_chat_preserves_decision_mapping(internal: str, reason: str, public: str) -> None:
    client, _ = client_for(upstream(internal, reason))
    response = client.post("/training/chat", json={"question": "질문"})
    assert response.status_code == 200
    assert response.json() == {
        "decision": public,
        "answer": "근거 기반 답변입니다.",
        "citations": [{"rank": 1, "label": "기초"}],
    }
    assert uuid.UUID(response.headers["x-request-id"])


def test_training_exception_maps_to_503() -> None:
    client, _ = client_for(RuntimeError("database unavailable"))
    assert client.post("/training/chat", json={"question": "질문"}).status_code == 503


@pytest.mark.asyncio
async def test_blocking_runtime_runs_off_the_event_loop_thread(monkeypatch) -> None:
    runtime = FakeRuntime(upstream())
    monkeypatch.setattr(training_rag, "get_training_runtime", lambda: runtime)
    event_loop_thread = threading.get_ident()
    await TrainingRagService().ask(question="질문", trace_id="trace")
    assert runtime.thread_id is not None
    assert runtime.thread_id != event_loop_thread


def test_importing_backend_does_not_load_training_ml() -> None:
    probe = (
        "import json,sys; import daengs_backend.main; "
        "print(json.dumps(sorted(m for m in sys.modules "
        "if m == 'torch' or m.startswith('sentence_transformers'))))"
    )
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout.strip().splitlines()[-1]) == []
