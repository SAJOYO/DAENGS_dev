"""산책 기록 **공동 조회** HTTP 경계 — `GET /app/pets/{pet_id}/walks[/{walk_id}]`.

판단은 `services/walk_group.py` 에 있습니다. **읽기만** 있고 쓰기는 없습니다 — 산책을 올리고
고치는 길은 계속 `/app/walks`(올린 사람 것)입니다. `/app/walks` 목록도 그대로 내 산책만 줍니다.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import CurrentAppUser
from daengs_backend.schemas.walk_group import (
    GroupWalkActor,
    GroupWalkDetail,
    GroupWalkItem,
    GroupWalkListResponse,
    GroupWalkPoint,
)
from daengs_backend.services import walk_group as walk_group_service
from daengs_backend.services.walk_session.chunk import decode_chunk

router = APIRouter(prefix="/app/pets", tags=["walks"])

_PET_NOT_FOUND = "강아지를 찾을 수 없습니다."
_WALK_NOT_FOUND = "산책 기록을 찾을 수 없습니다."


def _item_fields(shared: walk_group_service.GroupWalk) -> dict:
    walk = shared.walk
    return {
        "id": walk.id,
        "started_at": walk.started_at,
        "ended_at": walk.ended_at,
        "duration_s": int((walk.ended_at - walk.started_at).total_seconds()),
        "distance_m": shared.distance_m,
        "moving_s": shared.moving_s,
        "actor": GroupWalkActor(app_user_id=walk.app_user_id, nickname=shared.actor_nickname),
        "is_mine": shared.is_mine,
        "pet_ids": shared.pet_ids,
    }


@router.get("/{pet_id}/walks", response_model=GroupWalkListResponse)
async def list_pet_walks(
    pet_id: uuid.UUID,
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=walk_group_service.MAX_LIMIT)] = walk_group_service.DEFAULT_LIMIT,
) -> GroupWalkListResponse:
    """그 강아지가 나간 산책, 최근 순. **좌표는 없습니다.** 다른 보호자가 다녀온 것도 나옵니다."""
    try:
        page = await walk_group_service.list_page(
            session, user.app_user_id, pet_id, cursor=cursor, limit=limit
        )
    except walk_group_service.PetNotReadableError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _PET_NOT_FOUND) from None
    except walk_group_service.InvalidCursorError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "cursor 를 읽을 수 없습니다.") from None
    return GroupWalkListResponse(
        pet_id=pet_id,
        walks=[GroupWalkItem(**_item_fields(w)) for w in page.walks],
        next_cursor=page.next_cursor,
    )


@router.get("/{pet_id}/walks/{walk_id}", response_model=GroupWalkDetail)
async def get_pet_walk(
    pet_id: uuid.UUID,
    walk_id: uuid.UUID,
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> GroupWalkDetail:
    """한 건과 그 경로(시각·위도·경도만). 볼 수 없는 강아지이거나 그 강아지의 산책이 아니면 404."""
    try:
        shared = await walk_group_service.get_detail(session, user.app_user_id, pet_id, walk_id)
    except walk_group_service.PetNotReadableError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _PET_NOT_FOUND) from None
    except walk_group_service.WalkNotReadableError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _WALK_NOT_FOUND) from None
    points = sorted(
        (p for chunk in shared.walk.points for p in decode_chunk(chunk.payload)),
        key=lambda p: p.client_seq,
    )
    return GroupWalkDetail(
        **_item_fields(shared),
        points=[GroupWalkPoint(at=p.at, lat=p.lat, lng=p.lng) for p in points],
    )
