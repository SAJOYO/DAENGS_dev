"""Place discovery HTTP transport and CapabilityResult translation.

The consumer response contract and compact assistant projection live in sibling modules so this
adapter owns only the runtime boundary.  Public imports stay here for compatibility.
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from daengs_backend.config import settings
from daengs_backend.orchestration.adapters._place_contract import _DiscoveryResponse
from daengs_backend.orchestration.adapters._place_projection import (
    MAX_PLACE_CAPABILITY_BYTES,
    _candidate_count,
    project_place_capability_data,
)
from daengs_backend.orchestration.contracts import (
    CapabilityName,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    ErrorDetail,
    OutcomeDetail,
    PlacePayload,
)

_DISCOVERY_PATH = "/internal/place/discovery"
_DISCOVERY_RADIUS_M = 3_000


class PlaceCapabilityAdapter:
    capability = CapabilityName.PLACE

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        base_url: str | None = None,
        timeout_ms: int | None = None,
    ) -> None:
        self._client = client
        self._base_url = (
            settings.place_search_base_url if base_url is None else base_url
        ).rstrip("/")
        timeout = settings.place_discovery_timeout_ms if timeout_ms is None else timeout_ms
        if timeout <= 0:
            raise ValueError("Place discovery timeout must be positive")
        self._timeout_s = timeout / 1_000
        parsed = urlsplit(self._base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Place search base URL must be an absolute HTTP URL")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("Place search base URL must not include a path, query, or fragment")

    async def run(self, request: CapabilityRequest, *, request_id: str) -> CapabilityResult:
        started = time.perf_counter()
        payload = request.payload
        if not isinstance(payload, PlacePayload):
            return self._error(started, "invalid_payload", "장소 검색 요청이 올바르지 않습니다.")

        body = {
            "query": payload.query,
            "spatial": {
                "lat": payload.lat,
                "lng": payload.lon,
                "radius_m": _DISCOVERY_RADIUS_M,
            },
            # Profile identity and inferred profile values deliberately do not cross PR6.
            "conditions": None,
        }
        try:
            response = await self._post(body, request_id=request_id)
        except httpx.TimeoutException:
            return CapabilityResult(
                capability=self.capability,
                status=CapabilityStatus.TIMEOUT,
                error=ErrorDetail(
                    kind="place_discovery_timeout",
                    detail="장소 추천 응답 시간이 초과됐습니다.",
                ),
                elapsed_ms=_elapsed_ms(started),
            )
        except httpx.RequestError:
            return self._error(
                started,
                "place_discovery_unavailable",
                "장소 추천 기능에 연결할 수 없습니다.",
            )

        failure = _http_failure(response, started=started)
        if failure is not None:
            return failure
        try:
            discovery = _DiscoveryResponse.model_validate(response.json())
            data = project_place_capability_data(discovery)
        except (ValueError, TypeError, ValidationError, json.JSONDecodeError):
            return self._error(
                started,
                "place_discovery_invalid_response",
                "장소 추천 결과를 해석할 수 없습니다.",
            )

        candidate_count = _candidate_count(data)
        if candidate_count:
            return CapabilityResult(
                capability=self.capability,
                status=CapabilityStatus.OK,
                data=data,
                elapsed_ms=_elapsed_ms(started),
            )
        reason = (
            "place_needs_clarification"
            if discovery.planning.status == "needs_clarification"
            else "place_unsupported"
            if discovery.planning.status == "unsupported"
            else "place_no_candidates"
        )
        return CapabilityResult(
            capability=self.capability,
            status=CapabilityStatus.ABSTAINED,
            data=data,
            abstention=OutcomeDetail(code=reason, message=data["answer"]),
            elapsed_ms=_elapsed_ms(started),
        )

    async def _post(self, body: dict[str, Any], *, request_id: str) -> httpx.Response:
        url = f"{self._base_url}{_DISCOVERY_PATH}"
        kwargs = {
            "json": body,
            "headers": {"X-Request-ID": request_id},
            "timeout": self._timeout_s,
        }
        if self._client is not None:
            return await self._client.post(url, **kwargs)
        async with httpx.AsyncClient() as client:
            return await client.post(url, **kwargs)

    def _error(self, started: float, kind: str, detail: str) -> CapabilityResult:
        return CapabilityResult(
            capability=self.capability,
            status=CapabilityStatus.ERROR,
            error=ErrorDetail(kind=kind, detail=detail),
            elapsed_ms=_elapsed_ms(started),
        )


def _http_failure(response: httpx.Response, *, started: float) -> CapabilityResult | None:
    if response.is_success:
        return None
    code = _error_code(response)
    if response.status_code == 504 or code == "place_intent_provider_timeout":
        return CapabilityResult(
            capability=CapabilityName.PLACE,
            status=CapabilityStatus.TIMEOUT,
            error=ErrorDetail(
                kind="place_discovery_timeout",
                detail="장소 추천 응답 시간이 초과됐습니다.",
            ),
            elapsed_ms=_elapsed_ms(started),
        )
    if response.status_code == 503 and code == "place_intent_not_configured":
        kind = "place_discovery_not_configured"
        detail = "장소 추천 기능이 아직 설정되지 않았습니다."
    elif response.status_code >= 500:
        kind = "place_discovery_upstream_failure"
        detail = "장소 추천 기능을 현재 사용할 수 없습니다."
    else:
        kind = "place_discovery_invalid_response"
        detail = "장소 추천 요청을 처리할 수 없습니다."
    return CapabilityResult(
        capability=CapabilityName.PLACE,
        status=CapabilityStatus.ERROR,
        error=ErrorDetail(kind=kind, detail=detail),
        elapsed_ms=_elapsed_ms(started),
    )


def _error_code(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("detail"), dict):
        return None
    code = payload["detail"].get("code")
    return code if isinstance(code, str) else None


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1_000)

__all__ = [
    "MAX_PLACE_CAPABILITY_BYTES",
    "PlaceCapabilityAdapter",
    "project_place_capability_data",
]
