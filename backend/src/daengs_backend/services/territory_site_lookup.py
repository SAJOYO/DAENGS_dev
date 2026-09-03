"""place-search가 소유한 현행 점령지 게임판의 내부 HTTP 경계."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Protocol

import httpx

from daengs_backend.config import settings


class TerritorySiteUnavailableError(RuntimeError):
    """게임판 서비스가 응답하지 않거나 계약이 깨졌습니다."""


@dataclass(frozen=True)
class TerritorySiteSnapshot:
    site_id: str
    lat: Decimal
    lng: Decimal


class TerritorySiteLookup(Protocol):
    async def find_near_capture(
        self,
        *,
        site_id: str,
        lat: Decimal,
        lng: Decimal,
    ) -> TerritorySiteSnapshot | None: ...


class HttpTerritorySiteLookup:
    """50m 주변 결과에서 요청한 site_id의 서버 소유 좌표를 찾습니다."""

    def __init__(self, base_url: str, *, timeout_seconds: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    async def find_near_capture(
        self,
        *,
        site_id: str,
        lat: Decimal,
        lng: Decimal,
    ) -> TerritorySiteSnapshot | None:
        if not self._base_url:
            raise TerritorySiteUnavailableError("점령지 게임판 주소가 설정되지 않았습니다.")
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    f"{self._base_url}/territory/sites/nearby",
                    params={
                        "lat": str(lat),
                        "lng": str(lng),
                        # 공개 API의 최소 조회 반경. 실제 인증은 서비스가 다시 10m로 계산합니다.
                        "radius_m": 50,
                        "limit": 100,
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TerritorySiteUnavailableError("점령지 게임판을 확인할 수 없습니다.") from exc

        if not isinstance(payload, dict):
            raise TerritorySiteUnavailableError("점령지 게임판 응답 형식이 올바르지 않습니다.")
        sites = payload.get("sites")
        if not isinstance(sites, list):
            raise TerritorySiteUnavailableError("점령지 게임판 응답 형식이 올바르지 않습니다.")
        for site in sites:
            if not isinstance(site, dict) or site.get("site_id") != site_id:
                continue
            try:
                site_lat = Decimal(str(site["lat"]))
                site_lng = Decimal(str(site["lng"]))
                if not site_lat.is_finite() or not site_lng.is_finite():
                    raise InvalidOperation
                if not Decimal(-90) <= site_lat <= Decimal(90):
                    raise InvalidOperation
                if not Decimal(-180) <= site_lng <= Decimal(180):
                    raise InvalidOperation
                return TerritorySiteSnapshot(site_id=site_id, lat=site_lat, lng=site_lng)
            except (InvalidOperation, KeyError, ValueError):
                raise TerritorySiteUnavailableError(
                    "점령지 좌표 응답 형식이 올바르지 않습니다."
                ) from None
        return None


def get_territory_site_lookup() -> TerritorySiteLookup:
    return HttpTerritorySiteLookup(
        settings.territory_site_base_url,
        timeout_seconds=settings.territory_site_timeout_seconds,
    )
