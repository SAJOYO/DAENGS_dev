"""Training boundary translation. Domain retrieval and safety policy stay upstream."""

from __future__ import annotations

import time

from daengs_backend.orchestration.contracts import (
    CapabilityName,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    ErrorDetail,
    OutcomeDetail,
    TrainingPayload,
)
from daengs_backend.services.training_rag import (
    TrainingRagService,
    TrainingRagTimeoutError,
    TrainingRagUnavailableError,
)


class TrainingCapabilityAdapter:
    capability = CapabilityName.TRAINING

    def __init__(self, service: TrainingRagService | None = None) -> None:
        self._service = service or TrainingRagService()

    async def run(self, request: CapabilityRequest, *, request_id: str) -> CapabilityResult:
        started = time.perf_counter()
        payload = request.payload
        if not isinstance(payload, TrainingPayload):
            return self._error(started, "invalid_payload", "Training payload is invalid")
        try:
            upstream = await self._service.ask(question=payload.question, trace_id=request_id)
        except TrainingRagTimeoutError:
            return CapabilityResult(
                capability=self.capability,
                status=CapabilityStatus.TIMEOUT,
                error=ErrorDetail(kind="training_timeout", detail="훈련 응답 시간이 초과됐습니다."),
                elapsed_ms=_elapsed_ms(started),
            )
        except TrainingRagUnavailableError:
            return self._error(started, "training_unavailable", "훈련 기능을 사용할 수 없습니다.")
        except Exception as exc:  # noqa: BLE001 - normalize an unexpected boundary failure
            return self._error(started, type(exc).__name__, "훈련 기능 실행에 실패했습니다.")

        data = {
            "answer": upstream.answer,
            "citations": [citation.model_dump() for citation in upstream.citations],
        }
        if upstream.decision == "ANSWER":
            return CapabilityResult(
                capability=self.capability,
                status=CapabilityStatus.OK,
                data=data,
                elapsed_ms=_elapsed_ms(started),
            )
        if upstream.decision == "UNCERTAIN":
            return CapabilityResult(
                capability=self.capability,
                status=CapabilityStatus.ABSTAINED,
                data=data,
                abstention=OutcomeDetail(code=upstream.reason, message=upstream.answer),
                elapsed_ms=_elapsed_ms(started),
            )
        if upstream.decision in {"SAFETY_REFUSAL", "MEDICAL_REFUSAL"}:
            return CapabilityResult(
                capability=self.capability,
                status=CapabilityStatus.REFUSED,
                data=data,
                refusal=OutcomeDetail(code=upstream.reason, message=upstream.answer),
                elapsed_ms=_elapsed_ms(started),
            )
        return self._error(started, "unknown_training_decision", "훈련 결과를 해석할 수 없습니다.")

    def _error(self, started: float, kind: str, detail: str) -> CapabilityResult:
        return CapabilityResult(
            capability=self.capability,
            status=CapabilityStatus.ERROR,
            error=ErrorDetail(kind=kind, detail=detail),
            elapsed_ms=_elapsed_ms(started),
        )


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1_000)


__all__ = ["TrainingCapabilityAdapter"]
