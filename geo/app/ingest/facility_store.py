"""facility 기반층 적재 공용 — (source, source_ref) UPSERT + 미확인 행 정리.

**왜 DELETE-후-INSERT가 아닌가**: `facility.id`가 매 동기화마다 바뀌면 즐겨찾기·
추천 이력·딥링크가 전부 끊긴다. 원천 고유키로 UPSERT하면 id가 유지된다.
스냅샷 의미(원천에서 사라진 건 없어져야 함)는 `synced_at` 스탬프로 지킨다 —
이번 실행에서 안 건드려진 같은 원천 행만 지운다.

**보존 규칙**: 이번 실행이 값을 안 가져온 필드는 기존 값을 덮지 않는다.
KTO 상세(pet)처럼 쿼터 때문에 나눠 받는 데이터가 다음 실행에 증발하면 안 된다.
"""

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

COLUMNS = (
    "name", "kind", "category3", "sido", "sigungu", "address", "phone", "homepage",
    "hours_text", "closed_days", "parking", "indoor", "outdoor", "lat", "lng",
    "last_written", "pet", "raw",
)

_UPSERT = text("""
INSERT INTO facility (source, source_ref, name, kind, category3, sido, sigungu, address,
                      phone, homepage, hours_text, closed_days, parking, indoor, outdoor,
                      pet, location, last_written, snapshot, raw, synced_at)
VALUES (:source, :source_ref, :name, :kind, :category3, :sido, :sigungu, :address,
        :phone, :homepage, :hours_text, :closed_days, :parking, :indoor, :outdoor,
        CAST(:pet AS jsonb), ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
        :last_written, :snapshot, CAST(:raw AS jsonb), :synced_at)
ON CONFLICT (source, source_ref) WHERE source_ref IS NOT NULL DO UPDATE SET
    name         = EXCLUDED.name,
    kind         = EXCLUDED.kind,
    category3    = EXCLUDED.category3,
    sido         = COALESCE(EXCLUDED.sido, facility.sido),
    sigungu      = COALESCE(EXCLUDED.sigungu, facility.sigungu),
    address      = COALESCE(EXCLUDED.address, facility.address),
    phone        = COALESCE(EXCLUDED.phone, facility.phone),
    homepage     = COALESCE(EXCLUDED.homepage, facility.homepage),
    hours_text   = COALESCE(EXCLUDED.hours_text, facility.hours_text),
    closed_days  = COALESCE(EXCLUDED.closed_days, facility.closed_days),
    parking      = COALESCE(EXCLUDED.parking, facility.parking),
    indoor       = COALESCE(EXCLUDED.indoor, facility.indoor),
    outdoor      = COALESCE(EXCLUDED.outdoor, facility.outdoor),
    -- 빈 상세는 기존 상세를 덮지 않는다 (쿼터 때문에 나눠 받는다)
    pet          = CASE WHEN EXCLUDED.pet = '{}'::jsonb THEN facility.pet ELSE EXCLUDED.pet END,
    location     = EXCLUDED.location,
    last_written = COALESCE(EXCLUDED.last_written, facility.last_written),
    snapshot     = EXCLUDED.snapshot,
    raw          = COALESCE(EXCLUDED.raw, facility.raw),
    synced_at    = EXCLUDED.synced_at
""")


async def upsert_rows(
    session: AsyncSession, source: str, rows: list[dict], snapshot: str, synced_at: datetime
) -> int:
    """행 목록을 UPSERT. rows 는 COLUMNS + source_ref 키를 가진 dict."""
    for row in rows:
        row.setdefault("raw", None)
        row.setdefault("pet", "{}")
        row.update(source=source, snapshot=snapshot, synced_at=synced_at)
    for start in range(0, len(rows), 1000):
        await session.execute(_UPSERT, rows[start : start + 1000])
    return len(rows)


async def prune_unseen(session: AsyncSession, source: str, synced_at: datetime) -> int:
    """이번 실행에서 안 건드려진 같은 원천 행 삭제 = 스냅샷 의미.

    증분 적용(변경분만 UPSERT)에서는 부르면 안 된다 — 안 바뀐 행이 전부 지워진다.
    """
    result = await session.execute(
        text("""DELETE FROM facility
                WHERE source = :source
                  AND (synced_at IS NULL OR synced_at < :synced_at)"""),
        {"source": source, "synced_at": synced_at},
    )
    return result.rowcount
