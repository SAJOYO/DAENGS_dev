"""앱용 점령지 읽기 API. 원천·시설 종류·내부 행 ID는 노출하지 않는다."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_place.core.db import get_session
from daengs_place.territory.contracts import TerritorySitePage
from daengs_place.territory.sites import find_nearby

router = APIRouter(prefix="/territory/sites", tags=["territory-sites"])

MAX_LIMIT = 500


class NearbyTerritorySitesParams(BaseModel):
    lat: float = Field(ge=32, le=40)
    lng: float = Field(ge=123, le=133)
    radius_m: float = Field(1000, ge=50, le=3000)
    limit: int = Field(200, ge=1, le=MAX_LIMIT)


@router.get("/nearby", response_model=TerritorySitePage)
async def nearby_territory_sites(
    params: Annotated[NearbyTerritorySitesParams, Query()],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> TerritorySitePage:
    sites, truncated = await find_nearby(
        db,
        lat=params.lat,
        lng=params.lng,
        radius_m=params.radius_m,
        limit=params.limit,
    )
    return TerritorySitePage(count=len(sites), truncated=truncated, sites=sites)
