"""Resolve a page of site IDs from Place without leaking owners or capture locations."""

import json
from decimal import Decimal
from typing import Protocol

import httpx

from daengs_backend.config import settings
from daengs_backend.schemas.territory_owned import OwnedTerritoryLocation
from daengs_backend.services.territory_site_lookup import (
    TerritorySiteSnapshot,
    TerritorySiteUnavailableError,
)


class TerritorySiteBatchLookup(Protocol):
    async def find_by_ids(self, site_ids: list[str]) -> dict[str, TerritorySiteSnapshot]: ...


class HttpTerritorySiteBatchLookup:
    def __init__(self, base_url: str, *, timeout_seconds: float):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout_seconds

    async def find_by_ids(self, site_ids):
        if not site_ids:
            return {}
        if not self.base_url:
            raise TerritorySiteUnavailableError("점령지 게임판 주소가 설정되지 않았습니다.")
        try:
            async with (
                httpx.AsyncClient(timeout=self.timeout) as client,
                client.stream(
                    "GET",
                    f"{self.base_url}/territory/sites/by-ids",
                    params=[("site_ids", site_id) for site_id in site_ids],
                ) as response,
            ):
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 128_000:
                        raise ValueError("site response too large")
            payload = json.loads(body)
            if not isinstance(payload, dict) or not isinstance(payload.get("sites"), list):
                raise TypeError("invalid site page")
            found = {}
            for row in payload["sites"]:
                if not isinstance(row, dict):
                    raise TypeError("invalid site")
                site_id = row.get("site_id")
                if not isinstance(site_id, str) or site_id not in site_ids or site_id in found:
                    raise ValueError("unexpected or duplicate site")
                location = OwnedTerritoryLocation.model_validate(row)
                found[site_id] = TerritorySiteSnapshot(
                    site_id,
                    Decimal(str(location.lat)),
                    Decimal(str(location.lng)),
                )
            return found
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise TerritorySiteUnavailableError("점령지 좌표를 불러오지 못했습니다.") from exc


def get_territory_site_batch_lookup() -> TerritorySiteBatchLookup:
    return HttpTerritorySiteBatchLookup(
        settings.territory_site_base_url,
        timeout_seconds=settings.territory_site_timeout_seconds,
    )
