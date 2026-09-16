"""산책 기록 **공동 조회** HTTP 경계 — `GET /app/pets/{pet_id}/walks[/{walk_id}]` 와 통합 목록
`GET /app/pet-walks`.

판단은 `services/walk_group.py` 에 있습니다. **읽기만** 있고 쓰기는 없습니다 — 산책을 올리고
고치는 길은 계속 `/app/walks`(올린 사람 것)입니다. `/app/walks` 목록도 그대로 내 산책만 줍니다.
"""

import uuid
from datetime import date
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import CurrentAppUser
from daengs_backend.schemas.walk_group import (
    GroupWalkActor,
    GroupWalkCarer,
    GroupWalkDetail,
    GroupWalkFeedItem,
    GroupWalkFeedResponse,
    GroupWalkItem,
    GroupWalkListResponse,
    GroupWalkPoint,
    GroupWalkPreviewPoint,
    GroupWalkTotals,
)
from daengs_backend.services import walk_group as walk_group_service
from daengs_backend.services.walk_session.chunk import decode_chunk

router = APIRouter(prefix="/app/pets", tags=["walks"])
#: 강아지 하나가 아니라 **내가 볼 수 있는 강아지 전부**의 산책. `/app/pets/{pet_id}` 아래에 두면
#: `walks` 가 pet_id 자리에 걸리므로 `/app/pet-invites` 처럼 따로 둡니다.
feed_router = APIRouter(prefix="/app/pet-walks", tags=["walks"])

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


#: 반복 조건 하나에 받는 값 개수 상한. 날씨 코드는 0~99 전부를 보낼 수 있어야 합니다.
_MAX_REPEATED = 100


def _unprocessable(message: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, message)


@feed_router.get("", response_model=GroupWalkFeedResponse)
async def list_walk_feed(
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    pet_id: Annotated[list[uuid.UUID] | None, Query()] = None,
    actor_id: Annotated[list[uuid.UUID] | None, Query()] = None,
    date_from: date | None = None,
    date_through: date | None = None,
    tz: Annotated[str, Query(min_length=1, max_length=64)] = "UTC",
    month: Annotated[list[int] | None, Query()] = None,
    weather_code: Annotated[list[int] | None, Query()] = None,
    weather_missing: bool = False,
    exclude_mine: bool = False,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=walk_group_service.MAX_LIMIT)] = walk_group_service.DEFAULT_LIMIT,
) -> GroupWalkFeedResponse:
    """볼 수 있는 강아지 전부의 산책, 최근 순 — 산책 기록 화면 「산책별」의 통합 목록.

    **좌표는 썸네일용 축약본(`route_preview`)만** 싣습니다. 합계(`totals`)는 페이지가 아니라 조건
    전체이고, 보호자 후보(`carers`)는 조건과 무관하게 볼 수 있는 강아지 전부 기준입니다. 날짜·월은
    `tz` 시간대로 자릅니다. 볼 수 없는 강아지 id 는 범위를 넓히지 않고 결과에서 빠질 뿐입니다.
    """
    values = {"pet_id": pet_id, "actor_id": actor_id, "month": month, "weather_code": weather_code}
    if any(v is not None and len(v) > _MAX_REPEATED for v in values.values()):
        raise _unprocessable("조건 값이 너무 많습니다.")
    if month and any(not 1 <= m <= 12 for m in month):
        raise _unprocessable("month 는 1~12 입니다.")
    if weather_code and any(not 0 <= c <= 99 for c in weather_code):
        raise _unprocessable("weather_code 는 0~99 입니다.")
    if date_from and date_through and date_from > date_through:
        raise _unprocessable("date_from 이 date_through 보다 늦습니다.")
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise _unprocessable("tz 를 읽을 수 없습니다.") from None

    query = walk_group_service.WalkFeedQuery(
        pet_ids=tuple(pet_id or ()),
        actor_ids=tuple(actor_id or ()),
        date_from=date_from,
        date_through=date_through,
        tz=tz,
        months=tuple(month or ()),
        weather_codes=tuple(weather_code or ()),
        weather_missing=weather_missing,
        exclude_mine=exclude_mine,
    )
    try:
        feed = await walk_group_service.feed_page(session, user.app_user_id, query, cursor=cursor, limit=limit)
    except walk_group_service.InvalidCursorError:
        raise _unprocessable("cursor 를 읽을 수 없습니다.") from None
    return GroupWalkFeedResponse(
        walks=[
            GroupWalkFeedItem(
                **(_item_fields(w.group) | {"pet_ids": w.pet_ids}),
                weather_code=w.group.walk.weather_code,
                is_day=w.group.walk.is_day,
                temperature_c=w.group.walk.temperature_c,
                route_preview=[[GroupWalkPreviewPoint(lat=lat, lng=lng) for lat, lng in seg] for seg in w.route_preview],
            )
            for w in feed.walks
        ],
        totals=GroupWalkTotals(
            count=feed.totals.count, distance_m=feed.totals.distance_m, duration_s=feed.totals.duration_s
        ),
        carers=[
            GroupWalkCarer(app_user_id=c.app_user_id, nickname=c.nickname, is_me=c.is_me, pet_ids=c.pet_ids)
            for c in feed.carers
        ],
        next_cursor=feed.next_cursor,
    )
