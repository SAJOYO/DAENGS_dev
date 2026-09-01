"""산책 기록 HTTP 경계 — 서비스의 예외를 상태 코드로 바꿉니다.

판단은 여기 없습니다. "이미 올라온 것인가"·"이 강아지가 내 것인가"는
services/walk.py 가 정합니다.

**경로가 `/app/walks` 인 이유**는 앱 회원 전용이기 때문입니다 (`/app/pets` 와 같은
규칙). 이름이 `/walk` 와 비슷하지만 그쪽은 **산책 적합도(날씨 조언)** 라 하는 일이
전혀 다릅니다 — 기록은 복수형 `walks` 입니다.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import CurrentAppUser
from daengs_backend.models import Walk
from daengs_backend.schemas.walk import (
    WalkDetailResponse,
    WalkListResponse,
    WalkPointResponse,
    WalkPointsAppend,
    WalkResponse,
    WalkUpload,
)
from daengs_backend.services import walk as walk_service

router = APIRouter(prefix="/app/walks", tags=["walks"])


def _to_response(walk: Walk) -> WalkResponse:
    return WalkResponse(
        id=walk.id,
        client_session_id=walk.client_session_id,
        pet_ids=walk.pet_ids,
        started_at=walk.started_at,
        ended_at=walk.ended_at,
        weather_code=walk.weather_code,
        is_day=walk.is_day,
        temperature_c=walk.temperature_c,
    )


def _to_detail(walk: Walk) -> WalkDetailResponse:
    return WalkDetailResponse(
        **_to_response(walk).model_dump(),
        points=[
            WalkPointResponse(
                client_seq=point.client_seq,
                chain_index=point.chain_index,
                at=point.at,
                lat=point.lat,
                lng=point.lng,
                accuracy_m=point.accuracy_m,
                is_mock=point.is_mock,
            )
            for point in walk.points
        ],
    )


@router.get("", response_model=WalkListResponse)
async def list_walks(
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WalkListResponse:
    """내 산책 전부, 최근 순. **좌표는 없습니다.**

    앱이 이걸로 "서버에 뭐가 있나"를 보고, 기기에 없는 것만 한 건씩 받아 갑니다.
    """
    walks = await walk_service.list_walks(session, user.app_user_id)
    return WalkListResponse(walks=[_to_response(w) for w in walks])


@router.post("", response_model=WalkDetailResponse)
async def upload_walk(
    body: WalkUpload,
    response: Response,
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WalkDetailResponse:
    """산책 한 건을 올립니다.

    **같은 것을 다시 올려도 안전합니다.** 새로 만들었으면 201, 이미 있던 것이면
    200 과 함께 있던 것을 돌려줍니다 — 앱은 둘 다 "올라갔다"로 봅니다.
    실패했을 때만 다시 시도하면 되도록 이렇게 둡니다.
    """
    walk, created = await walk_service.upload_walk(session, user.app_user_id, body)
    response.status_code = (
        status.HTTP_201_CREATED if created else status.HTTP_200_OK
    )
    return _to_detail(walk)


@router.get("/{walk_id}", response_model=WalkDetailResponse)
async def get_walk(
    walk_id: uuid.UUID,
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WalkDetailResponse:
    """한 건과 그 경로. 내 것이 아니면 404 입니다."""
    try:
        walk = await walk_service.get_walk(session, user.app_user_id, walk_id)
    except walk_service.WalkNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "산책 기록을 찾을 수 없습니다.") from None
    return _to_detail(walk)


@router.post("/{walk_id}/points", response_model=WalkDetailResponse)
async def append_points(
    walk_id: uuid.UUID,
    body: WalkPointsAppend,
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WalkDetailResponse:
    """좌표를 이어 붙입니다. **긴 산책을 나눠 올릴 때** 씁니다.

    두 시간 산책이 좌표 5천 점(약 650KB)이고 촘촘히 잡히면 1MB 를 넘습니다. 한 번에
    보내면 nginx 바디 한도에 걸려 그 산책이 영영 안 올라갑니다.

    같은 묶음을 다시 보내도 안전합니다 — 이미 있는 순번은 넘어갑니다.
    """
    try:
        walk = await walk_service.append_points(session, user.app_user_id, walk_id, body)
    except walk_service.WalkNotFoundError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "산책 기록을 찾을 수 없습니다."
        ) from None
    return _to_detail(walk)
