"""상태 확인. 앱이 떴는지와 DB 에 닿는지를 같이 봅니다."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    """DB 가 죽었으면 503 을 돌려줍니다.

    200 인데 db 만 error 로 주면 모니터링이 실패를 못 잡습니다.
    compose 의 backend 에는 healthcheck 를 걸지 않았으므로,
    이 503 때문에 컨테이너가 재시작되는 일은 없습니다.
    """
    try:
        await session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        # SQLAlchemyError 만 잡으면 안 됩니다. 쿼리 자체가 아니라 '연결'에서
        # 실패하면 asyncpg 예외(InvalidPasswordError)나 OSError(ConnectionRefused)가
        # 감싸이지 않고 그대로 올라옵니다. 상태 확인 엔드포인트가 500 을 내면
        # "DB 가 죽었다"와 "앱이 깨졌다"를 구분할 수 없게 됩니다.
        logger.exception("상태 확인: DB 에 닿지 못했습니다")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "degraded", "db": "down"}

    return {"status": "ok", "db": "ok"}
