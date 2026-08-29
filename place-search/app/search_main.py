"""Place 검색 전용 진입점 — `uvicorn app.search_main:app`.

`main.py` 는 walk/journey/static-map/anchor 까지 전부 mount 하고 route provider 설정을
기동 게이트로 검증한다. 그 문을 같이 쓰면 Place 검색만 필요한 배포에서도 TMAP 키 하나
때문에 서버가 안 뜬다 — 코드 경계(결정 #73)를 끊어도 프로세스 경계가 남아 있던 자리다.

이 진입점의 약속: **PostGIS 만 있으면 뜬다.** 지도/route provider 키도, LLM 키도,
usage 미들웨어도 없다 — Place 검색은 외부 provider 를 호출하지 않는다. 이 약속은
tests/test_search_closure.py 가 import closure 로 집행한다.
"""

import asyncio
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import places_v2
from app.core.db import get_session

app = FastAPI(title="DAENGS Place Search", version="0.1.0")
app.include_router(places_v2.router)


@app.get("/health")
async def health():
    """Liveness only. provider 개념이 없는 서버라 설정을 에코하지 않는다 (main.py 와 다름)."""
    return {"ok": True}


@app.get("/health/ready")
async def readiness(db: Annotated[AsyncSession, Depends(get_session)]):
    """Ready to serve DB-backed requests — 이 서버의 의존은 DB 하나뿐이다."""
    try:
        async with asyncio.timeout(2):
            await db.execute(text("SELECT 1"))
    except (SQLAlchemyError, TimeoutError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database unavailable",
        ) from exc
    return {"ok": True, "database": "ready"}
