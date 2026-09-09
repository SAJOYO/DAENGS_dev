"""MOIS 존재 권위를 강제하는 canonical 의료 resolver."""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_place.geo.contract import SearchMust, SearchPlan
from daengs_place.geo.schemas import PlaceOut
from daengs_place.geo.search import find_authoritative_places
from daengs_place.place.source_catalog import MOIS_SOURCES as SOURCES


async def resolve_medical_places(
    db: AsyncSession,
    *,
    lat: float,
    lng: float,
    radius_m: int,
    kind: str,
    limit: int,
    judge_at: datetime,
    name_query: str = "",
    precise_order: bool = False,
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
            name_query=name_query,
        )),
        source=source.source,
        precise_order=precise_order,
    )
