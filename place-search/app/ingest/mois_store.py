"""행정안전부 정규화 레코드를 place에 멱등 UPSERT한다."""

import json
from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingest.mois import MoisRecord

_UPSERT_WITH_POINT = text("""
WITH point AS (
    SELECT ST_Transform(ST_SetSRID(ST_MakePoint(:x, :y), 5174), 4326) AS geom
)
INSERT INTO place (
    kind, name, address, phone, location, is_night, is_24h, hours, tags,
    source, source_id, source_updated_at, license_status_code, license_status_name,
    coordinate_source, raw_data, active, updated_at
)
SELECT
    :kind, :name, :address, :phone, point.geom::geography, false, false, NULL,
    CAST(:tags AS text[]), :source, :source_id, :source_updated_at,
    :license_status_code, :license_status_name, 'mois:epsg5174',
    CAST(:raw_data AS jsonb), :active, now()
FROM point
WHERE ST_X(point.geom) BETWEEN 123 AND 133
  AND ST_Y(point.geom) BETWEEN 32 AND 40
ON CONFLICT (source, source_id) DO UPDATE SET
    kind = EXCLUDED.kind,
    name = EXCLUDED.name,
    address = COALESCE(EXCLUDED.address, place.address),
    phone = COALESCE(EXCLUDED.phone, place.phone),
    location = EXCLUDED.location,
    tags = EXCLUDED.tags,
    source_updated_at = EXCLUDED.source_updated_at,
    license_status_code = EXCLUDED.license_status_code,
    license_status_name = EXCLUDED.license_status_name,
    coordinate_source = EXCLUDED.coordinate_source,
    raw_data = EXCLUDED.raw_data,
    active = EXCLUDED.active,
    updated_at = now()
RETURNING id
""")

_UPDATE_WITHOUT_POINT = text("""
UPDATE place SET
    kind = :kind,
    name = :name,
    address = COALESCE(:address, address),
    phone = COALESCE(:phone, phone),
    tags = CAST(:tags AS text[]),
    source_updated_at = :source_updated_at,
    license_status_code = :license_status_code,
    license_status_name = :license_status_name,
    raw_data = CAST(:raw_data AS jsonb),
    active = :active,
    updated_at = now()
WHERE source = :source AND source_id = :source_id
RETURNING id
""")


class MoisStore:
    def __init__(self, session: AsyncSession):
        self._session = session

    @staticmethod
    def _params(record: MoisRecord) -> dict:
        return {
            "kind": record.kind,
            "name": record.name,
            "address": record.address,
            "phone": record.phone,
            "tags": list(record.tags),
            "source": record.source,
            "source_id": record.source_id,
            "source_updated_at": record.source_updated_at,
            "license_status_code": record.license_status_code,
            "license_status_name": record.license_status_name,
            "raw_data": json.dumps(record.raw_data, ensure_ascii=False),
            "active": record.active,
        }

    async def upsert(self, record: MoisRecord) -> bool:
        params = self._params(record)
        if record.x_5174 is not None and record.y_5174 is not None:
            result = await self._session.execute(
                _UPSERT_WITH_POINT,
                {**params, "x": record.x_5174, "y": record.y_5174},
            )
            if result.scalar_one_or_none() is not None:
                return True

        # 좌표가 사라진 폐업/휴업 행도 기존 POI의 상태는 갱신한다. 신규 행은 격리된다.
        result = await self._session.execute(_UPDATE_WITHOUT_POINT, params)
        return result.scalar_one_or_none() is not None

    async def get_watermark(self, source: str) -> str | None:
        result = await self._session.execute(
            text("SELECT watermark FROM ingest_state WHERE source = :source"),
            {"source": source},
        )
        return result.scalar_one_or_none()

    async def set_watermark(self, source: str, watermark: str) -> None:
        await self._session.execute(
            text("""
                INSERT INTO ingest_state (source, watermark, updated_at)
                VALUES (:source, :watermark, now())
                ON CONFLICT (source) DO UPDATE SET
                    watermark = EXCLUDED.watermark,
                    updated_at = now()
            """),
            {"source": source, "watermark": watermark},
        )

    async def deactivate_missing(self, source: str, seen_ids: Sequence[str]) -> int:
        if not seen_ids:
            raise ValueError("refusing to deactivate an entire source from an empty snapshot")
        result = await self._session.execute(
            text("""
                UPDATE place SET active = false, updated_at = now()
                WHERE source = :source
                  AND source_id IS NOT NULL
                  AND NOT (source_id = ANY(CAST(:seen_ids AS text[])))
                  AND active IS true
            """),
            {"source": source, "seen_ids": list(seen_ids)},
        )
        return result.rowcount or 0

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
