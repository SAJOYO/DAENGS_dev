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
        """One capability run = one telemetry trace.

        The trace id is random and request-scoped (telemetry module docstring); it is not
        the orchestration ``request_id`` and never reaches the ``CapabilityResult``.  The
        result itself is built exactly as before — the two records emitted here are the
        only addition.  The import is function-local so the planning layer stays free of
        ``daengs_training`` at import time (test_importing_the_planning_layer_stays_light).
        """
        from daengs_training import telemetry

        started = time.perf_counter()
        with telemetry.training_trace() as trace:
            result = await self._execute(request, request_id=request_id, started=started)
            trace.emit(telemetry.EVENT_ADAPTER_TOTAL, duration_ms=_elapsed_ms(started))
            trace.emit(
                telemetry.EVENT_FINAL,
                result_status=result.status.value,
                code=_outcome_code(result),
                elapsed_ms=result.elapsed_ms,
            )
        return result

    async def _execute(
        self, request: CapabilityRequest, *, request_id: str, started: float
    ) -> CapabilityResult:
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


def _outcome_code(result: CapabilityResult) -> str:
    """The canonical reason/kind code already carried by the result — never its message."""
    if result.abstention is not None:
        return result.abstention.code
    if result.refusal is not None:
        return result.refusal.code
    if result.error is not None:
        return result.error.kind
    return "-"


__all__ = ["TrainingCapabilityAdapter"]
