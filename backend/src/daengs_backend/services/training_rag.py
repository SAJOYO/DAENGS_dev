"""Public-contract adapter for the process-local Training RAG component."""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from typing import Protocol

from daengs_backend.schemas.training import TrainingChatResponse, TrainingCitation

logger = logging.getLogger(__name__)


class TrainingRagUnavailableError(Exception):
    """The local Training runtime could not retrieve or generate an answer."""


class TrainingRuntime(Protocol):
    def answer(self, question: str, top_k: int = 4): ...  # noqa: ANN201


@lru_cache(maxsize=1)
def get_training_runtime() -> TrainingRuntime:
    """Initialize the heavy runtime lazily, on the first worker-thread request."""
    from daengs_training.service import RAGService

    return RAGService()


def release_training_runtime() -> None:
    """Release the singleton reference during backend shutdown."""
    get_training_runtime.cache_clear()


def _citation_label(heading_path: list[str], rank: int) -> str:
    parts = [part.strip() for part in heading_path if part.strip()]
    return parts[-1] if parts else f"훈련 근거 {rank}"


def _answer_locally(question: str) -> TrainingChatResponse:
    upstream = get_training_runtime().answer(question, top_k=4)
    decision = upstream.decision
    if decision == "REFUSE":
        decision = {
            "safety_boundary_training_harm": "SAFETY_REFUSAL",
            "safety_boundary_medical": "MEDICAL_REFUSAL",
        }.get(upstream.reason, "UNCERTAIN")
    citations = [
        TrainingCitation(
            rank=item.rank,
            label=_citation_label(item.heading_path, item.rank),
        )
        for item in upstream.evidence
    ]
    return TrainingChatResponse(decision=decision, answer=upstream.answer, citations=citations)


class TrainingRagService:
    async def ask(self, *, question: str, trace_id: str) -> TrainingChatResponse:
        """Run all blocking ML, PGVector, and Gemini work outside the event loop."""
        try:
            response = await asyncio.to_thread(_answer_locally, question)
        except Exception as exc:  # noqa: BLE001 - dependencies fail heterogeneously
            logger.exception("training_rag unavailable trace_id=%s", trace_id)
            raise TrainingRagUnavailableError from exc
        logger.info(
            "training_rag completed trace_id=%s decision=%s citations=%s",
            trace_id,
            response.decision,
            len(response.citations),
        )
        return response
