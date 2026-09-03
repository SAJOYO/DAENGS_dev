"""Public-contract adapter for the process-local Training RAG component."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

from daengs_backend.schemas.training import TrainingChatResponse, TrainingCitation, TrainingDecision

logger = logging.getLogger(__name__)


class TrainingRagUnavailableError(Exception):
    """The local Training runtime could not retrieve or generate an answer."""


class TrainingRagTimeoutError(TrainingRagUnavailableError):
    """The local Training runtime exceeded a typed upstream deadline."""


@dataclass(frozen=True)
class TrainingRagResult:
    """Backend-internal Training result with the domain reason preserved."""

    decision: TrainingDecision
    reason: str
    answer: str
    citations: list[TrainingCitation]

    def to_public_response(self) -> TrainingChatResponse:
        """Keep the existing `/training/chat` response shape unchanged."""
        return TrainingChatResponse(
            decision=self.decision,
            answer=self.answer,
            citations=self.citations,
        )


class TrainingRuntime(Protocol):
    def answer(self, question: str, top_k: int = 4): ...


@lru_cache(maxsize=1)
def get_training_runtime() -> TrainingRuntime:
    """Initialize the heavy runtime lazily, on the first worker-thread request.

    The body runs only when the cache misses, so it is the one place that knows this
    invocation *constructed* the runtime.  It marks the active telemetry trace instead of
    inspecting ``cache_info()``: concurrent first calls each run the body (lru_cache does not
    coalesce in-flight misses) and each will report ``created`` — that is the behavior that
    exists, and this card only observes it.
    """
    from daengs_training import telemetry
    from daengs_training.service import RAGService

    telemetry.current_trace().runtime_created = True
    return RAGService()


def release_training_runtime() -> None:
    """Release the singleton reference during backend shutdown."""
    get_training_runtime.cache_clear()


def _citation_label(heading_path: list[str], rank: int) -> str:
    parts = [part.strip() for part in heading_path if part.strip()]
    return parts[-1] if parts else f"훈련 근거 {rank}"


def _answer_locally(question: str) -> TrainingRagResult:
    # Import the domain timeout lazily with the heavy Training runtime boundary.
    from daengs_training import telemetry
    from daengs_training.service import TrainingTimeoutError

    trace = telemetry.current_trace()
    with trace.stage(telemetry.EVENT_RUNTIME) as stage:
        runtime = get_training_runtime()
        stage["runtime_state"] = "created" if trace.runtime_created else "reused"
    try:
        upstream = runtime.answer(question, top_k=4)
    except TrainingTimeoutError as exc:
        raise TrainingRagTimeoutError from exc
    decision = upstream.decision
    if decision == "REFUSE":
        # `no_results` is the one evidence-shortage REFUSE emitted by the current
        # retrieval gate. Every other REFUSE stays a refusal; silently treating a
        # new safety reason as evidence shortage would weaken the domain boundary.
        decision = {
            "no_results": "UNCERTAIN",
            "safety_boundary_training_harm": "SAFETY_REFUSAL",
            "safety_boundary_medical": "MEDICAL_REFUSAL",
            "output_safety_guardrail": "SAFETY_REFUSAL",
        }.get(upstream.reason, "SAFETY_REFUSAL")
    citations = [
        TrainingCitation(
            rank=item.rank,
            label=_citation_label(item.heading_path, item.rank),
        )
        for item in upstream.evidence
    ]
    return TrainingRagResult(
        decision=decision,
        reason=upstream.reason,
        answer=upstream.answer,
        citations=citations,
    )


class TrainingRagService:
    async def ask(self, *, question: str, trace_id: str) -> TrainingRagResult:
        """Run all blocking ML, PGVector, and Gemini work outside the event loop.

        ``training_trace()`` joins the adapter's trace when one is active and starts one
        for the public ``/training/chat`` path otherwise; ``to_thread`` copies the context,
        so the worker thread sees the same trace.  Function-local import: this module must
        not pull ``daengs_training`` when the backend is merely imported.
        """
        from daengs_training import telemetry

        with telemetry.training_trace():
            try:
                response = await asyncio.to_thread(_answer_locally, question)
            except TrainingRagTimeoutError:
                logger.exception("training_rag timeout trace_id=%s", trace_id)
                raise
            except Exception as exc:  # dependencies fail heterogeneously
                logger.exception("training_rag unavailable trace_id=%s", trace_id)
                raise TrainingRagUnavailableError from exc
        logger.info(
            "training_rag completed trace_id=%s decision=%s citations=%s",
            trace_id,
            response.decision,
            len(response.citations),
        )
        return response
