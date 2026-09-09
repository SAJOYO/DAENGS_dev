"""응급 병원 연락의 HTTP 경계.

**place-search 에 새 endpoint 를 만들지 않는다.** `/v2/places/search` 는 `kinds=["hospital"]`
을 받으면 이미 `resolve_medical_places`(MOIS 권위 원천만)로 가고 `facts.phone` 을 실어 준다.
LLM 을 하나도 거치지 않으므로 응급 경로에 모델 호출이 0회다 — Place discovery 를 쓰지 않는
이유가 그것이다(그쪽은 intent proposer 를 태운다).

**반경이 Place discovery 의 3km 와 다르다.** 응급에 "반경 안에 없습니다" 는 답이 되면 안 되고,
결과는 어차피 거리순이라 넓혀도 가까운 것부터 나온다. 지방에서 3km 는 0곳이 흔하다. 대신
후보마다 `distance_m` 을 실어, 25km 짜리를 "가까운 병원" 으로 읽지 않게 한다.

**`data` 에 평점·리뷰·추천 이유 필드를 두지 않는다.** 그런 필드가 없는 것이 이 계약의 목적이다.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from daengs_backend.config import settings
from daengs_backend.orchestration.contracts import (
    CapabilityName,
    CapabilityRequest,
    CapabilityResult,
    CapabilityStatus,
    ErrorDetail,
    OutcomeDetail,
    VetContactPayload,
)
from daengs_backend.orchestration.redirects import (
    SCOPED_REDIRECT_MESSAGES,
    VET_CONTACT_CALL_FIRST,
    VET_CONTACT_CURRENT_LOCATION_FRAME,
    VET_CONTACT_HOURS_UNKNOWN,
    VET_CONTACT_LOCATION_UNKNOWN,
)

_SEARCH_PATH = "/v2/places/search"
_RADIUS_M = 10_000
_LIMIT_PER_KIND = 5
_EMERGENCY_OPENER = SCOPED_REDIRECT_MESSAGES["emergency"]

_HOURS_UNKNOWN_NOTICE = {
    "code": "vet_contact.hours_unknown",
    "message": VET_CONTACT_HOURS_UNKNOWN,
}
_LOCATION_FRAME_NOTICE = {
    "code": "place.searched_around_current_location",
    "message": f"{VET_CONTACT_CURRENT_LOCATION_FRAME} 가까운 순으로 찾았습니다.",
}


class VetContactCapabilityAdapter:
    capability = CapabilityName.VET_CONTACT

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
            raise ValueError("vet contact timeout must be positive")
        self._timeout_s = timeout / 1_000

    async def run(self, request: CapabilityRequest, *, request_id: str) -> CapabilityResult:
        started = time.perf_counter()
        payload = request.payload
        if not isinstance(payload, VetContactPayload):
            return self._error(started, "invalid_payload", "응급 병원 요청이 올바르지 않습니다.")

        if payload.lat is None or payload.lon is None:
            # 되묻지 않는다. 클라이언트는 문장이 아니라 이 code 를 보고 위치 설정 CTA 를 단다.
            return CapabilityResult(
                capability=self.capability,
                status=CapabilityStatus.ABSTAINED,
                abstention=OutcomeDetail(
                    code="vet_contact.location_required",
                    message=f"{_EMERGENCY_OPENER}\n{VET_CONTACT_LOCATION_UNKNOWN}",
                ),
                elapsed_ms=_elapsed_ms(started),
            )

        body = {
            "lat": payload.lat,
            "lng": payload.lon,
            "radius_m": _RADIUS_M,
            "kinds": ["hospital"],
            "limit_per_kind": _LIMIT_PER_KIND,
        }
        try:
            response = await self._post(body, request_id=request_id)
        except httpx.TimeoutException:
            return CapabilityResult(
                capability=self.capability,
                status=CapabilityStatus.TIMEOUT,
                error=ErrorDetail(
                    kind="vet_contact_timeout",
                    detail="병원 목록 응답 시간이 초과됐습니다.",
                ),
                elapsed_ms=_elapsed_ms(started),
            )
        except httpx.RequestError:
            return self._error(
                started, "vet_contact_unavailable", "병원 목록 기능에 연결할 수 없습니다."
            )

        if not response.is_success:
            kind = (
                "vet_contact_upstream_failure"
                if response.status_code >= 500
                else "vet_contact_invalid_request"
            )
            return self._error(started, kind, "병원 목록을 가져오지 못했습니다.")

        try:
            candidates = _candidates(response.json())
        except (ValueError, TypeError, KeyError):
            return self._error(
                started, "vet_contact_invalid_response", "병원 목록을 해석할 수 없습니다."
            )

        return CapabilityResult(
            capability=self.capability,
            status=CapabilityStatus.OK,
            data={
                "answer": _answer(candidates, at_night=payload.at_night),
                "searched_radius_m": _RADIUS_M,
                "at_night": payload.at_night,
                "candidates": candidates,
                "notices": [dict(_HOURS_UNKNOWN_NOTICE), dict(_LOCATION_FRAME_NOTICE)],
            },
            elapsed_ms=_elapsed_ms(started),
        )

    async def _post(self, body: dict[str, Any], *, request_id: str) -> httpx.Response:
        url = f"{self._base_url}{_SEARCH_PATH}"
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


def _candidates(payload: Any) -> list[dict[str, Any]]:
    """v2 검색 응답에서 연락에 필요한 것만 꺼낸다.

    `open_now` 는 **싣되 지금은 전량 None** 이다. MOIS 적재가 `place.hours` 를 NULL 로 쓰기
    때문인데(`ingest/mois_store.py`), 계약에 자리를 두면 진료시간 원천이 생겼을 때 계약
    변경 없이 채워진다. 모르는 것을 없는 것으로 바꾸지 않는다.
    """
    if not isinstance(payload, dict):
        raise TypeError("search response must be an object")
    out: list[dict[str, Any]] = []
    for group in payload.get("groups") or []:
        for hit in group.get("results") or []:
            place = hit["place"]
            facts = place.get("facts") or {}
            medical = facts.get("medical") or {}
            out.append(
                {
                    "name": place["name"],
                    "phone": facts.get("phone"),
                    "distance_m": place["distance_m"],
                    "address": facts.get("address"),
                    "license_status_name": medical.get("license_status_name"),
                    "open_now": medical.get("open_now"),
                }
            )
    return out


def _answer(candidates: list[dict[str, Any]], *, at_night: bool) -> str:
    """네 줄. 첫 줄은 `redirects` 의 것이고 셋째 줄은 조건 없이 나간다."""
    lines = [_EMERGENCY_OPENER, VET_CONTACT_CALL_FIRST[at_night], VET_CONTACT_HOURS_UNKNOWN]
    if candidates:
        lines.append(
            f"{VET_CONTACT_CURRENT_LOCATION_FRAME} 가까운 순으로 {len(candidates)}곳을 찾았습니다."
        )
    else:
        lines.append(
            f"{VET_CONTACT_CURRENT_LOCATION_FRAME} 찾아봤지만 "
            f"반경 {_RADIUS_M // 1000}km 안에서는 등록된 동물병원을 찾지 못했습니다."
        )
    return "\n".join(lines)


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1_000)


__all__ = ["VetContactCapabilityAdapter"]
