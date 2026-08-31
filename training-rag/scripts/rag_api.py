"""Production-shaped local API for the PGVector + grounded-generation RAG.

Run locally:
    uv run uvicorn scripts.rag_api:app --host 127.0.0.1 --port 8000

The endpoint deliberately does not persist questions or model answers.  It uses
the evaluated PGVector corpus, serializes local inference for the 6 GB GPU, and
keeps the medical and output guardrails outside the model's control.
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

try:  # Supports both `uvicorn scripts.rag_api:app` and direct script execution.
    from scripts import runtime_generation as generation
    from scripts.pgvector_runtime import RuntimeRetriever
except ModuleNotFoundError:  # pragma: no cover - convenience path for CLI users
    import runtime_generation as generation
    from pgvector_runtime import RuntimeRetriever


LOGGER = logging.getLogger(__name__)
DEFAULT_DSN = "postgresql://dog_rag:dog_rag_local@localhost:5433/dog_rag"
MAX_QUESTION_CHARS = 1_000
DEFAULT_SERVING_CORPUS = Path(__file__).resolve().parents[1] / "config" / "serving_corpus_v1.json"

# These are intentionally system-authored rather than model output.  The model
# is never called when retrieval is uncertain or the question crosses a safety
# boundary, so an apparent refusal cannot be a hallucinated one.
INSUFFICIENT_EVIDENCE_TEXT = (
    "지금 검색된 자료만으로는 질문에 안전하게 답할 근거가 충분하지 않습니다. "
    "상황을 조금 더 알려주시거나, 다른 방식으로 질문해 주세요."
)

# A safety refusal is not an evidence shortage, and must not borrow its wording.
# generation.REFUSAL_TEXT says "제공된 자료에는 이 질문에 답할 내용이 없습니다" —
# true when retrieval came back empty, false when the corpus holds the answer and
# a boundary term stopped the question.  Every gate REFUSE used to send that one
# sentence, so a boundary refusal told the reader the corpus was the reason.
SAFETY_BOUNDARY_TEXT = (
    "체벌이나 임의 투약처럼 반려견이 다칠 수 있는 방법은 안내하지 않습니다. "
    "같은 문제를 다루는 다른 방법을 물어보시면 검수된 훈련 자료 안에서 답해 드리겠습니다."
)

#: gate() reasons that mean "a boundary stopped this", not "the corpus is empty".
_SAFETY_BOUNDARY_REASONS = frozenset(
    {"safety_boundary_training_harm", "safety_boundary_medical"}
)


def model_reported_no_evidence(answer: str) -> bool:
    """Recognize a short, uncited model statement that the context is insufficient.

    Gemma may paraphrase the fallback instead of emitting one fixed sentence.
    Detect the shared structure (context/evidence negation, no citation) rather
    than a question-specific string. Substantive answers remain eligible when
    they contain a numbered citation.
    """
    compact = " ".join(answer.split())
    if len(compact) > 240 or re.search(r"\[\s*\d+\s*\]", compact):
        return False
    return bool(re.search(
        r"(?:제공된|검색된|지금\s+검색된)\s*자료[^.!?]{0,120}"
        r"(?:내용|정보|근거)[^.!?]{0,40}"
        r"(?:없(?:습니다|다)|부족(?:합니다|하다)|충분하지\s*않)",
        compact,
    ))


def load_serving_document_ids(path: Path = DEFAULT_SERVING_CORPUS) -> tuple[str, ...]:
    """Load the small, reviewed serving allow-list without deleting raw data."""
    try:
        import json
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"serving corpus manifest is unreadable: {path}") from exc
    document_ids = payload.get("document_ids")
    if payload.get("schema_version") != "serving-corpus-v1" or not isinstance(document_ids, list):
        raise RuntimeError(f"invalid serving corpus manifest: {path}")
    normalized = tuple(item for item in document_ids if isinstance(item, str) and item.strip())
    if not normalized or len(set(normalized)) != len(normalized):
        raise RuntimeError(f"serving corpus manifest needs unique document IDs: {path}")
    return normalized


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    # The verified local prompt fits four ranked training chunks.  More context
    # is not automatically better: an unrelated fifth chunk caused Gemma 3 4B
    # to reject an otherwise answerable potty-training question.
    top_k: int = Field(default=4, ge=1, le=4)


class EvidenceCard(BaseModel):
    rank: int
    chunk_id: str
    document_id: str
    chunk_index: int
    heading_path: list[str]
    score: float


class ChatResponse(BaseModel):
    request_id: str
    answer: str
    decision: Literal["ANSWER", "UNCERTAIN", "REFUSE", "MEDICAL_REFUSAL"]
    reason: str
    generated: bool
    model: str | None
    prompt_version: str | None
    evidence: list[EvidenceCard]
    gate: dict[str, Any]
    usage: dict[str, Any] | None
    output_guardrail_blocked: bool


class RAGService:
    """One process-local runtime; local GPU inference is intentionally serial."""

    def __init__(
        self,
        *,
        retriever: Any | None = None,
        client: Any | None = None,
        medical_terms: tuple[str, ...] | list[str] | None = None,
        whitelist_terms: tuple[str, ...] | list[str] | None = None,
        serving_document_ids: tuple[str, ...] | list[str] | None = None,
    ) -> None:
        self.serving_document_ids = tuple(serving_document_ids) if serving_document_ids is not None else (
            load_serving_document_ids()
        )
        self.retriever = retriever or RuntimeRetriever(
            dsn=os.getenv("RAG_PGVECTOR_DSN", DEFAULT_DSN),
            document_ids=self.serving_document_ids,
        )
        self.client = client or generation.load_ollama_answer_client()
        self.medical_terms = list(medical_terms) if medical_terms is not None else (
            generation.medical_guardrail.load_medical_terms_v2()
        )
        self.whitelist_terms = list(whitelist_terms) if whitelist_terms is not None else (
            generation.medical_guardrail.load_training_whitelist()
        )
        self._lock = Lock()

    @property
    def model_name(self) -> str:
        return str(getattr(self.client, "model_id", self.client.info.name))

    @staticmethod
    def _prompt_chunk(hit: dict[str, Any]) -> dict[str, Any]:
        metadata = hit.get("metadata") or {}
        heading_path = metadata.get("heading_path", []) if isinstance(metadata, dict) else []
        if not isinstance(heading_path, list):
            heading_path = []
        return {
            "doc_id": str(hit["document_id"]),
            "chunk_index": int(hit["chunk_index"]),
            "heading_path": [str(part) for part in heading_path],
            "text": str(hit["text"]),
            "citation_allowed": True,
        }

    @classmethod
    def _evidence_cards(cls, hits: list[dict[str, Any]]) -> list[EvidenceCard]:
        cards: list[EvidenceCard] = []
        for rank, hit in enumerate(hits, start=1):
            chunk = cls._prompt_chunk(hit)
            cards.append(
                EvidenceCard(
                    rank=rank,
                    chunk_id=str(hit["chunk_id"]),
                    document_id=chunk["doc_id"],
                    chunk_index=chunk["chunk_index"],
                    heading_path=chunk["heading_path"],
                    score=float(hit["score"]),
                )
            )
        return cards

    def _medical_response(self, request_id: str) -> ChatResponse:
        verdict = generation.medical_guardrail.apply_output_guardrail(
            generation.MEDICAL_REFUSAL_TEMPLATE,
            self.medical_terms,
            self.whitelist_terms,
        )
        return ChatResponse(
            request_id=request_id,
            answer=str(verdict.text),
            decision="MEDICAL_REFUSAL",
            reason="medical_input_guardrail",
            generated=False,
            model=None,
            prompt_version=None,
            evidence=[],
            gate={"decision": "REFUSE", "reason": "medical_input_guardrail"},
            usage=None,
            output_guardrail_blocked=verdict.is_blocked,
        )

    def answer(self, question: str, top_k: int = 4) -> ChatResponse:
        """Run the fixed safety → retrieval → generation sequence once."""
        question = question.strip()
        if not question:
            raise ValueError("question must not be blank")
        request_id = str(uuid.uuid4())

        # SentenceTransformer and the local Ollama model share limited GPU/RAM.
        # Serializing a request prevents two users from turning a usable 20-second
        # answer into OOMs or unbounded queue contention.
        with self._lock:
            medical = generation.medical_guardrail.classify_input_v2(
                question, self.medical_terms, self.whitelist_terms
            )
            if medical.is_medical:
                return self._medical_response(request_id)

            hits = self.retriever.search(question, top_k)
            gate = self.retriever.gate(question, hits)
            evidence = self._evidence_cards(hits)
            decision = str(gate.get("decision", "REFUSE"))

            if decision == "REFUSE":
                reason = str(gate.get("reason", "retrieval_refused"))
                return ChatResponse(
                    request_id=request_id,
                    answer=(
                        SAFETY_BOUNDARY_TEXT
                        if reason in _SAFETY_BOUNDARY_REASONS
                        else generation.REFUSAL_TEXT
                    ),
                    decision="REFUSE",
                    reason=reason,
                    generated=False,
                    model=None,
                    prompt_version=None,
                    evidence=[],
                    gate=gate,
                    usage=None,
                    output_guardrail_blocked=False,
                )
            if decision != "PASS":
                return ChatResponse(
                    request_id=request_id,
                    answer=INSUFFICIENT_EVIDENCE_TEXT,
                    decision="UNCERTAIN",
                    reason=str(gate.get("reason", "retrieval_uncertain")),
                    generated=False,
                    model=None,
                    prompt_version=None,
                    evidence=evidence,
                    gate=gate,
                    usage=None,
                    output_guardrail_blocked=False,
                )

            prompt = generation.build_prompt(
                question,
                [self._prompt_chunk(hit) for hit in hits],
                "answer",
            )
            record: dict[str, Any] = {"question": question, "usage": None}
            raw_answer = self.client.complete(prompt, record)
            if not raw_answer:
                raise generation.GenerationError("local generation returned an empty answer")
            output = generation.medical_guardrail.apply_output_guardrail(
                raw_answer, self.medical_terms, self.whitelist_terms
            )
            if model_reported_no_evidence(output.text):
                return ChatResponse(
                    request_id=request_id,
                    answer=INSUFFICIENT_EVIDENCE_TEXT,
                    decision="UNCERTAIN",
                    reason="model_reported_insufficient_evidence",
                    generated=False,
                    model=self.model_name,
                    prompt_version=generation.PROMPT_VERSION,
                    evidence=evidence,
                    gate=gate,
                    usage=record.get("usage"),
                    output_guardrail_blocked=False,
                )
            if output.is_blocked:
                return ChatResponse(
                    request_id=request_id,
                    answer=output.text,
                    decision="REFUSE",
                    reason="output_safety_guardrail",
                    generated=True,
                    model=self.model_name,
                    prompt_version=generation.PROMPT_VERSION,
                    evidence=evidence,
                    gate=gate,
                    usage=record.get("usage"),
                    output_guardrail_blocked=True,
                )
            return ChatResponse(
                request_id=request_id,
                answer=output.text,
                decision="ANSWER",
                reason="grounded_generation",
                generated=True,
                model=self.model_name,
                prompt_version=generation.PROMPT_VERSION,
                evidence=evidence,
                gate=gate,
                usage=record.get("usage"),
                output_guardrail_blocked=False,
            )


def _cors_origins() -> list[str]:
    configured = os.getenv(
        "CORS_ALLOW_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    )
    return [origin.strip() for origin in configured.split(",") if origin.strip()]


def create_app(service: RAGService | None = None) -> FastAPI:
    """Create an injectable app; tests provide a fake service without loading models."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.rag_service is None:
            app.state.rag_service = RAGService()
        yield

    app = FastAPI(
        title="Dog Training RAG API",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.rag_service = service
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    def current_service(request: Request) -> RAGService:
        runtime = request.app.state.rag_service
        if runtime is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="RAG runtime is starting",
            )
        return runtime

    @app.get("/healthz")
    async def healthz(request: Request) -> dict[str, str]:
        runtime = current_service(request)
        return {
            "status": "ok",
            "embedding_model": str(getattr(runtime.retriever, "model_name", "unknown")),
            "generation_model": runtime.model_name,
            "serving_document_count": str(len(runtime.serving_document_ids)),
        }

    @app.post("/chat", response_model=ChatResponse)
    async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
        if not payload.question.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="question must not be blank",
            )
        runtime = current_service(request)
        try:
            return await run_in_threadpool(runtime.answer, payload.question, payload.top_k)
        except generation.GenerationError as exc:
            LOGGER.warning("RAG runtime unavailable: %s", str(exc)[:200])
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="RAG generation is temporarily unavailable",
            ) from exc
        except Exception as exc:  # noqa: BLE001 - dependency failures are heterogeneous
            LOGGER.exception("RAG request failed")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="RAG retrieval is temporarily unavailable",
            ) from exc

    return app


app = create_app()
