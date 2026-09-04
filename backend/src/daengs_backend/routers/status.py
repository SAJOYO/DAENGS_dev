"""상태 페이지 HTTP 경계 (#180 · 로드맵 B1).

판단은 여기 없습니다. 무엇을 물어보고 어떤 상태로 부를지는 `services/status.py` 가 정하고,
여기서는 그 결과를 스키마로 옮기기만 합니다.

**권한은 `READ` 입니다** — `/admin/crawl` 의 조회와 같습니다. 읽기 전용이고 개인정보가
없습니다. `ops:write` 로 잠그면 "API 는 열려 있는데 화면만 안 보이는 계정"이 생깁니다
(`console/page.tsx` 의 카드 권한 주석이 그 이야기입니다).

**언제나 200 입니다.** 항목 하나가 죽어도 나머지를 돌려줍니다 — 그 판단이 이 화면의 전부라,
서비스가 예외를 밖으로 내보내지 않습니다. `/health` 가 DB 가 죽었을 때 503 을 주는 것과는
반대인데, 저기는 모니터링이 읽는 자리이고 여기는 사람이 읽는 자리라서 그렇습니다.
"""

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import Perm, Principal, require
from daengs_backend.core.warm_up import STATE_ATTR
from daengs_backend.schemas.status import StatusItemOut, StatusOut
from daengs_backend.services import status as status_service

router = APIRouter(prefix="/admin/status", tags=["admin-status"])


@router.get("", response_model=StatusOut)
async def status_(
    request: Request,
    _admin: Annotated[Principal, Depends(require(Perm.READ))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StatusOut:
    """컨테이너 · 모델 · 예산 · 크롤을 한 번에. 화면이 30초마다 폴링합니다."""
    items = await status_service.collect(
        session,
        # lifespan 이 놓아 둔 예열 결과. 없으면 서비스가 그것도 한 항목으로 말합니다.
        warm_up=getattr(request.app.state, STATE_ATTR, None),
    )
    return StatusOut(
        items=[StatusItemOut(**asdict(item)) for item in items],
        checked_at=status_service.checked_at(),
    )
