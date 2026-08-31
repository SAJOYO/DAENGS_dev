"""Walk boundary translation. GOOD, CAUTION, and UNSAFE are all successful verdicts."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

from daengs_backend.orchestration.adapters.life import _walk_life
from daengs_backend.orchestration.contracts import (
    CapabilityName,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    ErrorDetail,
    OutcomeDetail,
    WalkPayload,
)


class WalkCapabilityAdapter:
    capability = CapabilityName.WALK

    def __init__(self, walk: Callable[[WalkPayload], Any] | None = None) -> None:
        self._walk = walk or _walk_life

    async def run(self, request: CapabilityRequest, *, request_id: str) -> CapabilityResult:
        del request_id  # Walk currently needs location and the domain-owned clock only.
        started = time.perf_counter()
        payload = request.payload
        if not isinstance(payload, WalkPayload):
            return self._error(started, "invalid_payload", "Walk payload is invalid")
        try:
            upstream = await asyncio.to_thread(self._walk, payload)
        except TimeoutError:
            return CapabilityResult(
                capability=self.capability,
                status=CapabilityStatus.TIMEOUT,
                error=ErrorDetail(kind="walk_timeout", detail="산책 정보 응답 시간이 초과됐습니다."),
                elapsed_ms=_elapsed_ms(started),
            )
        except Exception as exc:  # noqa: BLE001 - normalize an unexpected boundary failure
            return self._error(started, type(exc).__name__, "산책 정보 기능 실행에 실패했습니다.")

        data = upstream.model_dump(mode="json", by_alias=True)
        if upstream.now.grade == "unknown":
            return CapabilityResult(
                capability=self.capability,
                status=CapabilityStatus.ABSTAINED,
                data=data,
                abstention=OutcomeDetail(
                    code="unknown_verdict",
                    message="현재 관측 자료만으로 산책 조건을 판단할 수 없습니다.",
                ),
                elapsed_ms=_elapsed_ms(started),
            )
        return CapabilityResult(
            capability=self.capability,
            status=CapabilityStatus.OK,
            data=data,
            elapsed_ms=_elapsed_ms(started),
        )

    def _error(self, started: float, kind: str, detail: str) -> CapabilityResult:
        return CapabilityResult(
            capability=self.capability,
            status=CapabilityStatus.ERROR,
            error=ErrorDetail(kind=kind, detail=detail),
            elapsed_ms=_elapsed_ms(started),
        )


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1_000)


__all__ = ["WalkCapabilityAdapter"]
