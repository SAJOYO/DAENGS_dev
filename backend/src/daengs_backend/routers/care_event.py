"""케어 로그 HTTP 경계 (`/app/care-events`) — 서비스의 예외를 상태 코드로 바꿉니다 (#332).

판단은 여기 없습니다. "같은 기록인가"·"이 강아지가 내 것인가"·"기간이 너무 넓은가" 는
services/care_event.py 가 정합니다.

**경로가 `/app/` 아래인 이유**는 앱 회원 전용이기 때문입니다 (`/app/pets` · `/app/walks` 와
같은 규칙). 라우터 자체가 `CurrentAppUser` 로 잠겨 있습니다.
"""

import uuid
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import CurrentAppUser
from daengs_backend.models import CareEvent
from daengs_backend.schemas.care_event import (
    CareDaySummaryResponse,
    CareEventCreate,
    CareEventListResponse,
    CareEventQuery,
    CareEventResponse,
)
from daengs_backend.services import care_event as care_service
from daengs_backend.services.pet import PetNotFoundError

router = APIRouter(prefix="/app/care-events", tags=["care-events"])

Session = Annotated[AsyncSession, Depends(get_session)]

_PET_NOT_FOUND = "강아지를 찾을 수 없습니다."
_EVENT_NOT_FOUND = "기록을 찾을 수 없습니다."


def _to_response(event: CareEvent) -> CareEventResponse:
    return CareEventResponse(
        id=event.id,
        pet_id=event.pet_id,
        kind=event.kind,
        occurred_at=event.occurred_at,
        note=event.note,
        client_event_id=event.client_event_id,
        created_at=event.created_at,
    )


def _range(start: datetime | None, end: datetime | None) -> CareEventQuery:
    """`from`/`to` 의 timezone 검사. 없으면 422 — 없는 채로 받으면 어느 하루인지 서버가 추측합니다."""
    try:
        return CareEventQuery(start=start, end=end)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None


@router.get("", response_model=CareEventListResponse)
async def list_events(
    user: CurrentAppUser,
    session: Session,
    pet_id: Annotated[uuid.UUID, Query()],
    start: Annotated[datetime | None, Query(alias="from")] = None,
    end: Annotated[datetime | None, Query(alias="to")] = None,
) -> CareEventListResponse:
    """기간 조회, **최근 먼저.** `from`/`to` 를 안 보내면 최근 7일이고, 한 번에 31일까지입니다.

    내 강아지가 아니면 404 입니다. 배웅한 아이의 기록도 그대로 보입니다.
    """
    window = _range(start, end)
    try:
        events, start_, end_ = await care_service.list_events(
            session, user.app_user_id, pet_id, start=window.start, end=window.end
        )
    except PetNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _PET_NOT_FOUND) from None
    except care_service.CareRangeError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    return CareEventListResponse(
        pet_id=pet_id, start=start_, end=end_, events=[_to_response(e) for e in events]
    )


@router.post("", response_model=CareEventResponse, status_code=status.HTTP_201_CREATED)
async def record_event(
    body: CareEventCreate,
    response: Response,
    user: CurrentAppUser,
    session: Session,
) -> CareEventResponse:
    """기록 한 건. **같은 것을 다시 보내도 안전합니다.**

    새로 만들었으면 201, 같은 `client_event_id` 가 이미 있으면 200 과 함께 있던 것을
    돌려줍니다 — 앱은 둘 다 "올라갔다" 로 봅니다 (`/app/walks` 와 같은 규칙).
    """
    try:
        event, created = await care_service.record(session, user.app_user_id, body)
    except PetNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _PET_NOT_FOUND) from None
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return _to_response(event)


# ⚠️ `/{event_id}` 보다 먼저 선언합니다. 지금은 메서드가 달라(GET 대 DELETE) 안 부딪히지만,
#    `/app/pets/primary` 가 그 순서 때문에 422 를 낸 적이 있어 같은 규칙을 지킵니다.
@router.get("/today", response_model=CareDaySummaryResponse)
async def today(
    user: CurrentAppUser,
    session: Session,
    pet_id: Annotated[uuid.UUID, Query()],
    day: Annotated[date | None, Query()] = None,
) -> CareDaySummaryResponse:
    """하루 요약 — "밥 2 · 약 1 · 간식 3 · 산책 1" 을 한 번에. 산책 수는 `walks` 에서 셉니다.

    `day` 를 안 보내면 서울 기준 오늘입니다. 하루의 경계도 서울 자정입니다
    (services/care_event.py 의 `DAY_TIMEZONE`).
    """
    try:
        summary = await care_service.day_summary(session, user.app_user_id, pet_id, day=day)
    except PetNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _PET_NOT_FOUND) from None
    return CareDaySummaryResponse(
        pet_id=pet_id,
        day=summary.day,
        timezone=summary.timezone,
        start=summary.start,
        end=summary.end,
        meal=summary.counts.get("meal", 0),
        medication=summary.counts.get("medication", 0),
        snack=summary.counts.get("snack", 0),
        walk=summary.walks,
        events=[_to_response(e) for e in summary.events],
    )


@router.delete("/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event(
    event_id: uuid.UUID,
    user: CurrentAppUser,
    session: Session,
) -> None:
    """지웁니다. 내 기록이 아니면 404 입니다 — 남의 것도 같은 404 라 존재 여부가 안 샙니다."""
    try:
        await care_service.delete_event(session, user.app_user_id, event_id)
    except care_service.CareEventNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _EVENT_NOT_FOUND) from None


__all__ = ["router"]
