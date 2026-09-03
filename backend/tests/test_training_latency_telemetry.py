"""Training stage timings are observable and change nothing else.

Every test here runs with fakes at the existing seams (runtime factory, retriever, answer
client, psycopg module).  No Gemini call, no E5 weights, no database.
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from daengs_backend.orchestration.adapters.training import TrainingCapabilityAdapter
from daengs_backend.orchestration.contracts import (
    CapabilityRequest,
    CapabilityStatus,
)
from daengs_backend.services import training_rag
from daengs_backend.services.training_rag import (
    TrainingRagService,
    TrainingRagTimeoutError,
    TrainingRagUnavailableError,
)
from daengs_training import telemetry
from daengs_training.generation import gemini as generation
from daengs_training.retrieval.pgvector import RuntimeRetriever
from daengs_training.service import ChatResponse, EvidenceCard, RAGService, TrainingTimeoutError

QUESTION = "고유식별질문 QUERYTOKEN99 강아지가 손을 물어요"
PROMPT_MARKER = "PROMPTTOKEN77"
ANSWER_MARKER = "ANSWERTOKEN55"
CHUNK_TEXT = "CHUNKTOKEN33 손을 물면 놀이를 바로 멈춥니다."
USER_ID = "app-user-7f3a"
PET_ID = "pet-91c2"
PROVIDER_PAYLOAD_MARKER = "PROVIDER_PAYLOAD_MARKER_SHOULD_NOT_LEAK"


# --------------------------------------------------------------------------- fixtures / fakes


@pytest.fixture
def records(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.INFO, logger=telemetry.LOGGER.name)
    return caplog


def events(caplog: pytest.LogCaptureFixture) -> list[dict]:
    return [
        record.training_event
        for record in caplog.records
        if record.name == telemetry.LOGGER.name and hasattr(record, "training_event")
    ]


def by_event(caplog: pytest.LogCaptureFixture, event: str) -> list[dict]:
    return [item for item in events(caplog) if item["event"] == event]


def upstream(decision: str = "UNCERTAIN", reason: str = "model_reported_insufficient_evidence"):
    return ChatResponse(
        request_id="runtime-request",
        answer="지금 검색된 자료만으로는 근거가 충분하지 않습니다.",
        decision=decision,
        reason=reason,
        generated=False,
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
        self.calls = 0

    def answer(self, question: str, top_k: int = 4) -> ChatResponse:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeRetriever:
    def __init__(self, decision: str = "PASS") -> None:
        self.decision = decision

    def search(self, question: str, top_k: int) -> list[dict]:
        return [{
            "chunk_id": "chunk-bite", "document_id": "doc-training", "chunk_index": 7,
            "text": CHUNK_TEXT, "metadata": {"heading_path": ["FAQ", "무는 행동"]}, "score": 0.87,
        }]

    def gate(self, question: str, results: list[dict]) -> dict:
        return {"decision": self.decision, "reason": "fixture", "top_score": 0.87}


class FakeClient:
    model_id = "gemini-3.1-flash-lite"
    info = generation.ClientInfo(name="gemini:gemini-3.1-flash-lite")

    def __init__(self, answer: str | Exception = f"[1] {ANSWER_MARKER} 놀이를 멈추세요.") -> None:
        self.answer = answer

    def complete(self, prompt: str, record: dict) -> str:
        assert PROMPT_MARKER not in prompt  # the marker below is only ever in exceptions
        record["usage"] = {"input_tokens": 10, "output_tokens": 12}
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def rag_service(client: FakeClient | None = None, decision: str = "PASS") -> RAGService:
    return RAGService(
        retriever=FakeRetriever(decision),
        client=client or FakeClient(),
        medical_terms=[],
        whitelist_terms=[],
        serving_document_ids=("fixture-doc",),
    )


def training_request(question: str = QUESTION) -> CapabilityRequest:
    return CapabilityRequest.model_validate(
        {"capability": "training", "payload": {"question": question}, "timeout_ms": None}
    )


@pytest.fixture
def real_cache():
    """Use the real lru_cache factory with a fake RAGService class; leave the cache clean."""
    import daengs_training.service as service_module

    original = service_module.RAGService
    constructed: list[FakeRuntime] = []

    class FakeRAGService(FakeRuntime):
        def __init__(self) -> None:
            super().__init__(upstream())
            constructed.append(self)

    service_module.RAGService = FakeRAGService  # type: ignore[misc]
    training_rag.get_training_runtime.cache_clear()
    try:
        yield constructed
    finally:
        training_rag.get_training_runtime.cache_clear()
        service_module.RAGService = original  # type: ignore[misc]


# --------------------------------------------------------------------------- runtime state


async def test_reused_runtime_timing_path(records, monkeypatch) -> None:
    runtime = FakeRuntime(upstream())
    monkeypatch.setattr(training_rag, "get_training_runtime", lambda: runtime)

    result = await TrainingCapabilityAdapter(TrainingRagService()).run(
        training_request(), request_id="orchestration-request-id"
    )

    assert result.status == CapabilityStatus.ABSTAINED
    assert result.abstention is not None
    assert result.abstention.code == "model_reported_insufficient_evidence"
    (runtime_event,) = by_event(records, telemetry.EVENT_RUNTIME)
    assert runtime_event["runtime_state"] == "reused"
    assert isinstance(runtime_event["duration_ms"], int)
    (final,) = by_event(records, telemetry.EVENT_FINAL)
    assert final["result_status"] == "ABSTAINED"
    assert final["code"] == "model_reported_insufficient_evidence"
    assert final["elapsed_ms"] == result.elapsed_ms
    (total,) = by_event(records, telemetry.EVENT_ADAPTER_TOTAL)
    assert total["duration_ms"] >= result.elapsed_ms
    assert [item["event"] for item in events(records)] == [
        telemetry.EVENT_RUNTIME,
        telemetry.EVENT_ADAPTER_TOTAL,
        telemetry.EVENT_FINAL,
    ]


def test_created_then_reused_runtime_timing_path(records, real_cache) -> None:
    with telemetry.training_trace():
        first = training_rag._answer_locally(QUESTION)
    with telemetry.training_trace():
        second = training_rag._answer_locally(QUESTION)

    assert first.decision == second.decision == "UNCERTAIN"
    assert len(real_cache) == 1  # the cache still constructs exactly once
    states = [item["runtime_state"] for item in by_event(records, telemetry.EVENT_RUNTIME)]
    assert states == ["created", "reused"]


def test_runtime_construction_failure_is_timed_and_propagates(records, monkeypatch) -> None:
    def broken_factory():
        raise generation.GenerationError("GEMINI_API_KEY is required")

    monkeypatch.setattr(training_rag, "get_training_runtime", broken_factory)
    with telemetry.training_trace(), pytest.raises(generation.GenerationError):
        training_rag._answer_locally(QUESTION)
    (runtime_event,) = by_event(records, telemetry.EVENT_RUNTIME)
    assert runtime_event["outcome"] == "error"
    assert runtime_event["error_type"] == "GenerationError"
    assert "GEMINI_API_KEY" not in str(runtime_event)


# --------------------------------------------------------------------------- lock wait


def test_lock_wait_is_measured_directly(records) -> None:
    service = rag_service()
    outcome: dict = {}

    def worker() -> None:
        with telemetry.training_trace():
            outcome["response"] = service.answer(QUESTION)

    service._lock.acquire()
    thread = threading.Thread(target=worker)
    thread.start()
    time.sleep(0.08)
    service._lock.release()
    thread.join(timeout=5)

    assert outcome["response"].decision == "ANSWER"
    (lock_event,) = by_event(records, telemetry.EVENT_LOCK_WAIT)
    assert lock_event["wait_ms"] >= 60
    assert not service._lock.locked()


def test_uncontended_lock_wait_is_near_zero(records) -> None:
    with telemetry.training_trace():
        rag_service().answer(QUESTION)
    (lock_event,) = by_event(records, telemetry.EVENT_LOCK_WAIT)
    assert 0 <= lock_event["wait_ms"] < 50


# --------------------------------------------------------------------------- generation


def test_gemini_success_timing(records) -> None:
    with telemetry.training_trace():
        response = rag_service().answer(QUESTION)
    assert response.decision == "ANSWER"
    (gen,) = by_event(records, telemetry.EVENT_GENERATION)
    assert gen["outcome"] == "success"
    assert isinstance(gen["duration_ms"], int)
    assert set(gen) == {"event", "trace_id", "duration_ms", "outcome"}


def test_gemini_timeout_timing_does_not_expose_payload(records) -> None:
    client = FakeClient(generation.GenerationTimeoutError(f"deadline {PROMPT_MARKER}"))
    with telemetry.training_trace(), pytest.raises(TrainingTimeoutError):
        rag_service(client).answer(QUESTION)
    (gen,) = by_event(records, telemetry.EVENT_GENERATION)
    assert gen["outcome"] == "timeout"
    assert "error_type" not in gen
    assert PROMPT_MARKER not in str(gen)
    assert not any(PROMPT_MARKER in record.getMessage() for record in records.records)


def test_gemini_error_timing_keeps_class_name_only(records) -> None:
    client = FakeClient(generation.GenerationError(f"Gemini call failed: {PROMPT_MARKER}"))
    with telemetry.training_trace(), pytest.raises(generation.GenerationError):
        rag_service(client).answer(QUESTION)
    (gen,) = by_event(records, telemetry.EVENT_GENERATION)
    assert gen["outcome"] == "error"
    assert gen["error_type"] == "GenerationError"
    assert PROMPT_MARKER not in str(gen)


def test_gate_refusal_emits_no_generation_record(records) -> None:
    with telemetry.training_trace():
        response = rag_service(decision="UNCERTAIN").answer(QUESTION)
    assert response.decision == "UNCERTAIN"
    assert by_event(records, telemetry.EVENT_LOCK_WAIT)
    assert by_event(records, telemetry.EVENT_GENERATION) == []


# --------------------------------------------------------------------------- retrieval split


class FakeCursor:
    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute(self, sql: str, params=None) -> None:
        self.executed.append(sql)

    def fetchall(self):
        return [("chunk-1", "doc-1", 0, CHUNK_TEXT, {"heading_path": []}, 0.9)]


@contextmanager
def _cursor_cm(cursor: FakeCursor):
    yield cursor


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self):
        return _cursor_cm(self._cursor)


class FakePsycopg:
    def __init__(self, cursor: FakeCursor) -> None:
        self.cursor = cursor
        self.dsns: list[str] = []

    @contextmanager
    def connect(self, dsn: str):
        self.dsns.append(dsn)
        yield FakeConnection(self.cursor)


def _retriever() -> tuple[RuntimeRetriever, FakeCursor]:
    """Build the retriever without __post_init__ — no SentenceTransformer, no psycopg."""
    retriever = object.__new__(RuntimeRetriever)
    cursor = FakeCursor()
    retriever.dsn = "postgresql://fake"
    retriever.model_name = "intfloat/multilingual-e5-base"
    retriever.embedding_label = None
    retriever.document_ids = ("doc-1",)
    retriever.label = retriever.model_name
    retriever.model = SimpleNamespace(encode=lambda text, normalize_embeddings: [0.1, 0.2])
    retriever.psycopg = FakePsycopg(cursor)
    return retriever, cursor


def test_retriever_emits_embedding_and_split_pgvector_timing(records) -> None:
    retriever, cursor = _retriever()
    with telemetry.training_trace():
        rows = retriever.search(QUESTION, top_k=4)

    assert [row["chunk_id"] for row in rows] == ["chunk-1"]
    assert cursor.executed[0] == "set local enable_indexscan = off"
    (embedding,) = by_event(records, telemetry.EVENT_EMBEDDING)
    assert set(embedding) == {"event", "trace_id", "duration_ms"}
    (pgvector,) = by_event(records, telemetry.EVENT_PGVECTOR)
    assert set(pgvector) == {"event", "trace_id", "duration_ms", "connect_ms", "query_ms"}
    assert pgvector["duration_ms"] >= pgvector["connect_ms"]
    assert pgvector["duration_ms"] >= pgvector["query_ms"]
    assert CHUNK_TEXT not in str(events(records))


def test_retriever_search_without_a_trace_is_silent(records) -> None:
    retriever, _ = _retriever()
    assert retriever.search(QUESTION, top_k=4)
    assert events(records) == []


# --------------------------------------------------------------------------- privacy / contract


async def test_no_query_or_identifier_content_in_any_record(records, monkeypatch) -> None:
    runtime = FakeRuntime(rag_service().answer(QUESTION))  # a real answer, produced off-record
    records.clear()
    monkeypatch.setattr(training_rag, "get_training_runtime", lambda: runtime)

    await TrainingCapabilityAdapter(TrainingRagService()).run(
        training_request(f"{QUESTION} {USER_ID} {PET_ID}"), request_id=USER_ID
    )

    assert events(records)
    text = "\n".join(record.getMessage() for record in records.records)
    text += str(events(records))
    for forbidden in (QUESTION, "QUERYTOKEN99", ANSWER_MARKER, CHUNK_TEXT, USER_ID, PET_ID):
        assert forbidden not in text
    allowed = telemetry._SAFE_FIELDS | {"event", "trace_id"}
    for item in events(records):
        assert set(item) <= allowed, item


def test_unknown_fields_are_rejected_before_they_can_leak() -> None:
    with pytest.raises(ValueError):
        telemetry.TrainingTrace().emit("training_final", question=QUESTION)


async def test_trace_id_is_random_shared_per_run_and_not_the_request_id(records, monkeypatch) -> None:
    monkeypatch.setattr(training_rag, "get_training_runtime", lambda: FakeRuntime(upstream()))
    adapter = TrainingCapabilityAdapter(TrainingRagService())
    await adapter.run(training_request(), request_id="request-A")
    first = {item["trace_id"] for item in events(records)}
    records.clear()
    await adapter.run(training_request(), request_id="request-A")
    second = {item["trace_id"] for item in events(records)}

    assert len(first) == len(second) == 1
    assert first != second
    (trace_id,) = first
    assert len(trace_id) == 16 and int(trace_id, 16) >= 0
    assert trace_id != "request-A"


async def test_capability_result_contract_is_unchanged(records, monkeypatch) -> None:
    monkeypatch.setattr(training_rag, "get_training_runtime", lambda: FakeRuntime(upstream()))
    result = await TrainingCapabilityAdapter(TrainingRagService()).run(
        training_request(), request_id="trace"
    )
    payload = result.model_dump(mode="json")
    assert set(payload) == {
        "capability", "status", "data", "abstention", "refusal", "job", "error", "elapsed_ms"
    }
    (trace_id,) = {item["trace_id"] for item in events(records)}
    assert trace_id not in str(payload)


async def test_timeout_and_unavailable_results_still_emit_final(records, monkeypatch) -> None:
    monkeypatch.setattr(
        training_rag, "get_training_runtime",
        lambda: FakeRuntime(TrainingTimeoutError("deadline")),
    )
    result = await TrainingCapabilityAdapter(TrainingRagService()).run(
        training_request(), request_id="trace"
    )
    assert result.status == CapabilityStatus.TIMEOUT
    (final,) = by_event(records, telemetry.EVENT_FINAL)
    assert final["result_status"] == "TIMEOUT"
    assert final["code"] == "training_timeout"


async def test_public_training_service_path_gets_its_own_trace(records, monkeypatch) -> None:
    """`/training/chat` calls the service without the adapter; it must still be traced."""
    monkeypatch.setattr(training_rag, "get_training_runtime", lambda: FakeRuntime(upstream()))
    await TrainingRagService().ask(question=QUESTION, trace_id="public")
    (runtime_event,) = by_event(records, telemetry.EVENT_RUNTIME)
    assert runtime_event["trace_id"] != "public"


async def test_service_timeout_type_is_preserved_with_tracing(monkeypatch) -> None:
    monkeypatch.setattr(
        training_rag, "get_training_runtime",
        lambda: FakeRuntime(TrainingTimeoutError("deadline")),
    )
    with pytest.raises(TrainingRagTimeoutError):
        await TrainingRagService().ask(question=QUESTION, trace_id="public")


async def test_timeout_log_excludes_chained_provider_message(caplog, monkeypatch) -> None:
    client = FakeClient(generation.GenerationTimeoutError(PROVIDER_PAYLOAD_MARKER))
    monkeypatch.setattr(training_rag, "get_training_runtime", lambda: rag_service(client))
    caplog.set_level(logging.ERROR, logger=training_rag.logger.name)

    with pytest.raises(TrainingRagTimeoutError):
        await TrainingRagService().ask(question=QUESTION, trace_id="public")

    assert PROVIDER_PAYLOAD_MARKER not in caplog.text
    assert "Traceback" not in caplog.text
    assert "error_type=TrainingRagTimeoutError" in caplog.text


async def test_unavailable_log_excludes_provider_message(caplog, monkeypatch) -> None:
    monkeypatch.setattr(
        training_rag,
        "get_training_runtime",
        lambda: FakeRuntime(generation.GenerationError(PROVIDER_PAYLOAD_MARKER)),
    )
    caplog.set_level(logging.ERROR, logger=training_rag.logger.name)

    with pytest.raises(TrainingRagUnavailableError):
        await TrainingRagService().ask(question=QUESTION, trace_id="public")

    assert PROVIDER_PAYLOAD_MARKER not in caplog.text
    assert "Traceback" not in caplog.text
    assert "error_type=GenerationError" in caplog.text


# --------------------------------------------------------------------------- visibility


def test_fallback_handler_is_installed_only_when_nothing_would_log(monkeypatch) -> None:
    monkeypatch.setattr(telemetry.LOGGER, "hasHandlers", lambda: False)
    before = list(telemetry.LOGGER.handlers)
    try:
        telemetry.TrainingTrace().emit(telemetry.EVENT_FINAL, result_status="OK")
        added = [h for h in telemetry.LOGGER.handlers if h not in before]
        assert len(added) == 1 and isinstance(added[0], logging.StreamHandler)
    finally:
        for handler in list(telemetry.LOGGER.handlers):
            if handler not in before:
                telemetry.LOGGER.removeHandler(handler)
    assert telemetry.LOGGER.level == logging.INFO
