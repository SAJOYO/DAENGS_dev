"""중립 점령지 주변 조회. 점령·방문·인증 상태는 여기서 만들지 않는다."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_place.territory.contracts import TerritorySite
from daengs_place.territory.grid import ACTIVE_SITE_ID_PREFIX

_NEARBY = text("""
WITH origin AS (
    SELECT ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography AS point
)
SELECT site_id,
       ST_Y(location::geometry) AS lat,
       ST_X(location::geometry) AS lng,
       ST_Distance(location, origin.point) AS distance_m
FROM territory_site
CROSS JOIN origin
WHERE site_id LIKE :active_site_pattern
  AND ST_DWithin(location, origin.point, :radius_m)
ORDER BY location <-> origin.point, site_id
LIMIT :limit
""")


async def find_nearby(
    db: AsyncSession,
    *,
    lat: float,
    lng: float,
    radius_m: float,
    limit: int,
) -> tuple[tuple[TerritorySite, ...], bool]:
    """현행 140u 게임판을 거리순으로 읽는다.

    ``radius_m``은 지도 선로딩 범위다. 사용자가 점령지에 닿았는지를 판정할 10m 정책과
    섞지 않는다. ``limit + 1``번째 행은 응답이 잘렸는지만 판단하고 내보내지 않는다.
    """
    rows = (
        await db.execute(
            _NEARBY,
            {
                "lat": lat,
                "lng": lng,
                "radius_m": radius_m,
                "limit": limit + 1,
                "active_site_pattern": f"{ACTIVE_SITE_ID_PREFIX}%",
            },
        )
    ).all()
    truncated = len(rows) > limit
    return (
        tuple(
            TerritorySite(
                site_id=row.site_id,
                lat=row.lat,
                lng=row.lng,
                distance_m=row.distance_m,
            )
            for row in rows[:limit]
        ),
        truncated,
    )
