"""Life boundary translation without copying retrieval or generation policy."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException

from daengs_backend.orchestration.contracts import (
    CapabilityName,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    ErrorDetail,
    LifePayload,
    OutcomeDetail,
    WalkPayload,
)


def _ask_life(question: str) -> Any:
    """Open the existing request-scoped dependencies around the Life service shim."""
    from daengs_life.app import deps
    from daengs_life.app.services import ask as life_service

    encoder = deps.get_encoder()
    connection_dependency = deps.get_conn()
    conn = next(connection_dependency)
    try:
        return life_service.ask(question, encoder=encoder, conn=conn)
    finally:
        connection_dependency.close()


def _walk_life(payload: WalkPayload) -> Any:
    """Call the existing deterministic Walk service with domain-owned dependencies."""
    from daengs_life.app.deps import get_cache, get_now
    from daengs_life.app.services.walk import walk
    from daengs_life.realtime.geo import LatLon

    return walk(LatLon(payload.lat, payload.lon), get_now(), cache=get_cache())


class LifeCapabilityAdapter:
    capability = CapabilityName.LIFE

    def __init__(self, ask: Callable[[str], Any] | None = None) -> None:
        self._ask = ask or _ask_life

    async def run(self, request: CapabilityRequest, *, request_id: str) -> CapabilityResult:
        del request_id  # Life does not currently consume trace context at its domain boundary.
        started = time.perf_counter()
        payload = request.payload
        if not isinstance(payload, LifePayload):
            return self._error(started, "invalid_payload", "Life payload is invalid")
        try:
            upstream = await asyncio.to_thread(self._ask, payload.question)
        except HTTPException as exc:
            detail = str(exc.detail)
            if exc.status_code == 404:
                return CapabilityResult(
                    capability=self.capability,
                    status=CapabilityStatus.ABSTAINED,
                    abstention=OutcomeDetail(code="no_evidence", message=detail),
                    elapsed_ms=_elapsed_ms(started),
                )
            if exc.status_code == 504:
                return CapabilityResult(
                    capability=self.capability,
                    status=CapabilityStatus.TIMEOUT,
                    error=ErrorDetail(kind="life_timeout", detail=detail),
                    elapsed_ms=_elapsed_ms(started),
                )
            return self._error(started, f"life_http_{exc.status_code}", detail)
        except TimeoutError:
            return CapabilityResult(
                capability=self.capability,
                status=CapabilityStatus.TIMEOUT,
                error=ErrorDetail(kind="life_timeout", detail="생활 정보 응답 시간이 초과됐습니다."),
                elapsed_ms=_elapsed_ms(started),
            )
        except Exception as exc:  # noqa: BLE001 - normalize an unexpected boundary failure
            return self._error(started, type(exc).__name__, "생활 정보 기능 실행에 실패했습니다.")

        citations = [
            {
                "label": hit.citation,
                "url": hit.citation_url,
                "document_title": hit.document_title,
            }
            for hit in upstream.hits
        ]
        return CapabilityResult(
            capability=self.capability,
            status=CapabilityStatus.OK,
            data={
                "answer": upstream.answer,
                "citations": citations,
                "quality": {"cited": upstream.cited, "ungrounded": upstream.ungrounded},
            },
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


__all__ = ["LifeCapabilityAdapter"]
