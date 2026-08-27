import asyncio
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import anchor, facility, places, places_v2, static_map
from app.core.config import settings
from app.core.db import get_session
from app.features.hospital import api as hospital
from app.features.journey import api as journey
from app.features.pharmacy import api as pharmacy
from app.features.walk import api as walk
from app.usage.composition import route_capability_problems
from app.usage.gate import usage_request_scope

_problems = route_capability_problems()
if _problems:
    # 설정만 받고 런타임에 추정으로 흘려보내면 장애와 구분이 안 된다. 여기서 세운다.
    raise RuntimeError("경로 제공사 설정 오류: " + " / ".join(_problems))

app = FastAPI(title="DAENGS_geo", version="0.1.0")
app.include_router(places_v2.router)
app.include_router(places.router)
app.include_router(facility.router)
app.include_router(hospital.router)
app.include_router(pharmacy.router)
app.include_router(walk.router)
app.include_router(journey.router)
app.include_router(static_map.router)
app.include_router(anchor.router)


@app.middleware("http")
async def bind_usage_request_scope(request, call_next):
    """요청당 사용량 카운터만 만든다. 허용·소비 집행은 실제 외부 호출 Gate 한 곳에서 한다."""
    async with usage_request_scope():
        return await call_next(request)


if settings.dev_console:
    _DEV = Path(__file__).parent / "static" / "dev.html"
    _ANCHORS = Path(__file__).parent / "static" / "anchors.html"
    _FACILITY = Path(__file__).parent / "static" / "facility.html"

    @app.get("/facility-map", include_in_schema=False)
    async def facility_map():
        """시설 필터를 눈으로 보는 표면. 개를 바꾸면 무엇이 빠지는지가 보여야 한다."""
        return FileResponse(_FACILITY, media_type="text/html")

    @app.get("/anchors", include_in_schema=False)
    async def anchor_map():
        """앵커 분포 눈으로 보기. 검증용 표면이라 dev_console 과 같은 게이트 뒤에 둔다."""
        return FileResponse(_ANCHORS, media_type="text/html")

    @app.get("/dev", include_in_schema=False)
    async def dev_console():
        """검증용 콘솔. 앱 UI가 아니다 — 루프(말→조건→화면)와 spots가 말이 되는지 눈으로 보는 용도."""
        return FileResponse(_DEV, media_type="text/html")


@app.get("/health")
async def health():
    """Liveness only. Dependencies belong to readiness, not process survival."""
    return {
        "ok": True,
        "map_provider": settings.map_provider,
        "usage_policy": settings.usage_policy,
    }


@app.get("/health/ready")
async def readiness(db: Annotated[AsyncSession, Depends(get_session)]):
    """Ready to serve DB-backed requests. External providers are deliberately excluded."""
    try:
        async with asyncio.timeout(2):
            await db.execute(text("SELECT 1"))
    except (SQLAlchemyError, TimeoutError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database unavailable",
        ) from exc
    return {"ok": True, "database": "ready"}
