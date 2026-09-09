"""Place 검색 전용 진입점 — `uvicorn daengs_place.main:app`.

DAENGS_geo의 통합 `app.main`은 walk/journey/static-map까지 전부 mount 하고 route provider
설정을 기동 게이트로 검증한다. 그 문을 같이 쓰면 Place 검색만 필요한 배포에서도 TMAP 키 하나
때문에 서버가 안 뜬다 — 코드 경계(결정 #73)를 끊어도 프로세스 경계가 남아 있던 자리다.

이 진입점의 약속: **PostGIS 만 있으면 기존 검색은 뜬다.** 내부 discovery만 요청 시점에
optional Gemini를 사용하며, 키 없음이나 provider 장애는 기존 공개 검색·health와 격리한다.
지도/route provider와 usage 미들웨어는 없다. 이 약속은 tests/place/test_boundary.py가
공개 경로와 import closure로 집행한다.
"""

import asyncio
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_place.api import (
    discovery_internal,
    facility_internal,
    filter_edits_internal,
    places_v2,
    places_v3,
    territory_sites,
)
from daengs_place.core.db import get_session

app = FastAPI(title="DAENGS Place Search", version="0.1.0")
app.include_router(places_v2.router)
app.include_router(places_v3.router)
app.include_router(territory_sites.router)
app.include_router(discovery_internal.router)
app.include_router(facility_internal.router)
app.include_router(filter_edits_internal.router)


@app.get("/health")
async def health():
    """Liveness only. Optional provider 설정은 노출하거나 검사하지 않는다."""
    return {"ok": True}


@app.get("/health/ready")
async def readiness(db: Annotated[AsyncSession, Depends(get_session)]):
    """Ready to serve DB-backed requests; optional discovery provider는 readiness 밖이다."""
    try:
        async with asyncio.timeout(2):
            await db.execute(text("SELECT 1"))
    except (SQLAlchemyError, TimeoutError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database unavailable",
        ) from exc
    return {"ok": True, "database": "ready"}
