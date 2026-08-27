"""GET /anchor/search — 화면 안 점령 앵커. 지도 표면 전용.

48만 개를 한 번에 못 내린다. 화면 bbox 로 자르고 상한을 둔다. 상한에 걸리면
`truncated=true` 로 알린다 — 조용히 자르면 "이 동네엔 앵커가 이만큼뿐"으로 읽힌다.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session

router = APIRouter(prefix="/anchor", tags=["anchor"])

MAX_LIMIT = 3000


class AnchorParams(BaseModel):
    south: float = Field(ge=32, le=40)
    west: float = Field(ge=123, le=133)
    north: float = Field(ge=32, le=40)
    east: float = Field(ge=123, le=133)
    kind: str | None = None                  # 한전주 등. 미지정 = 전부
    limit: int = Field(1500, ge=1, le=MAX_LIMIT)


class AnchorOut(BaseModel):
    id: int
    cell: str
    kind: str
    lat: float
    lng: float


class AnchorSearchOut(BaseModel):
    count: int
    truncated: bool                          # 상한에 걸렸나 — 밀도 해석을 오도하지 않으려고
    results: list[AnchorOut]


_SEARCH = text("""
SELECT id, cell, kind,
       ST_Y(location::geometry) AS lat, ST_X(location::geometry) AS lng
FROM anchor
WHERE location && ST_MakeEnvelope(:west, :south, :east, :north, 4326)::geography
  AND (CAST(:kind AS text) IS NULL OR kind = :kind)
LIMIT :limit
""")


@router.get("/search", response_model=AnchorSearchOut)
async def anchor_search(
    params: Annotated[AnchorParams, Query()],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> AnchorSearchOut:
    rows = (await db.execute(_SEARCH, {
        "south": params.south, "west": params.west,
        "north": params.north, "east": params.east,
        "kind": params.kind, "limit": params.limit + 1,
    })).all()
    truncated = len(rows) > params.limit
    rows = rows[: params.limit]
    return AnchorSearchOut(
        count=len(rows),
        truncated=truncated,
        results=[
            AnchorOut(id=r.id, cell=r.cell, kind=r.kind, lat=r.lat, lng=r.lng)
            for r in rows
        ],
    )
