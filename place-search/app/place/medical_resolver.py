"""MOIS 존재 권위를 강제하는 canonical 의료 resolver."""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.geo.contract import SearchMust, SearchPlan
from app.geo.schemas import PlaceOut
from app.geo.search import find_authoritative_places
from app.place.source_catalog import MOIS_SOURCES as SOURCES


async def resolve_medical_places(
    db: AsyncSession,
    *,
    lat: float,
    lng: float,
    radius_m: int,
    kind: str,
    limit: int,
    judge_at: datetime,
) -> list[PlaceOut]:
    """같은 kind의 dev/임의 source를 섞지 않고 해당 MOIS endpoint만 읽는다."""
    try:
        source = SOURCES[kind]  # type: ignore[index]
    except KeyError as exc:
        raise ValueError(f"unsupported medical kind: {kind}") from exc
    return await find_authoritative_places(
        db,
        SearchPlan(must=SearchMust(
            lat=lat,
            lng=lng,
            radius_m=radius_m,
            judge_at=judge_at,
            kind=kind,
            limit=limit,
        )),
        source=source.source,
    )
