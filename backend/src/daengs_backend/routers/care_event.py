"""케어 로그 HTTP 경계 (`/app/care-events`) — 서비스의 예외를 상태 코드로 바꿉니다 (#332).

판단은 여기 없습니다. "같은 기록인가"·"이 강아지가 내 것인가"·"기간이 너무 넓은가" 는
services/care_event.py 가 정합니다.

**경로가 `/app/` 아래인 이유**는 앱 회원 전용이기 때문입니다 (`/app/pets` · `/app/walks` 와
같은 규칙). 라우터 자체가 `CurrentAppUser` 로 잠겨 있습니다.
"""

import uuid
from collections.abc import Sequence
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import CurrentAppUser
from daengs_backend.models import CareEvent
from daengs_backend.schemas.care_event import (
    ActorOut,
    CareDaySummaryResponse,
    CareEventCreate,
    CareEventListResponse,
    CareEventQuery,
    CareEventResponse,
    DayWalkOut,
)
from daengs_backend.services import care_event as care_service
from daengs_backend.services import pet_member as member_service
from daengs_backend.services.pet import PetNotFoundError

router = APIRouter(prefix="/app/care-events", tags=["care-events"])

Session = Annotated[AsyncSession, Depends(get_session)]

_PET_NOT_FOUND = "강아지를 찾을 수 없습니다."
_EVENT_NOT_FOUND = "기록을 찾을 수 없습니다."


async def _actor_labels(
    session: AsyncSession, pet_ids: Sequence[uuid.UUID], events: Sequence[CareEvent]
) -> dict[uuid.UUID, str | None]:
    """actor id → 표시 이름. **등장하는 사람 수만큼만** `actor_label` 을 부릅니다.

    이벤트마다 부르면 목록 길이만큼 왕복합니다 — 아빠가 쓴 줄이 30개면 30번을 묻게 됩니다.
    같은 사람이 여러 줄을 남긴 경우가 흔하므로, 먼저 등장하는 `actor_app_user_id` 를
    집합으로 모아 **사람 수만큼만** 묻습니다. `None`(컬럼보다 먼저 쌓인 기록·탈퇴자)은
    `actor_label` 을 부를 것도 없이 `None` 이라, 애초에 집합에 넣지 않습니다.

    **판정은 논리 그룹 전체**입니다 (MVP 결정 §7) — 연결된 상대는 내 행의 구성원이 아니라
    그 사람이 적은 기록의 이름이 통째로 빕니다 (`group_actor_label` 독스트링).
    """
    ids = {e.actor_app_user_id for e in events if e.actor_app_user_id is not None}
    return {
        uid: await member_service.group_actor_label(session, list(pet_ids), uid)
        for uid in ids
    }


def _to_response(
    event: CareEvent, labels: dict[uuid.UUID, str | None] | None = None
) -> CareEventResponse:
    labels = labels or {}
    actor_id = event.actor_app_user_id
    return CareEventResponse(
        id=event.id,
        pet_id=event.pet_id,
        kind=event.kind,
        occurred_at=event.occurred_at,
        note=event.note,
        client_event_id=event.client_event_id,
        created_at=event.created_at,
        actor=ActorOut(app_user_id=actor_id, nickname=labels.get(actor_id) if actor_id else None),
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
        events, start_, end_, group_ids = await care_service.list_events(
            session, user.app_user_id, pet_id, start=window.start, end=window.end
        )
    except PetNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _PET_NOT_FOUND) from None
    except care_service.CareRangeError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    labels = await _actor_labels(session, group_ids, events)
    return CareEventListResponse(
        pet_id=pet_id,
        start=start_,
        end=end_,
        events=[_to_response(e, labels) for e in events],
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

    **약(`medication`) 은 6시간 창 안에 같은 종류가 있으면 409 입니다** (docs/co-care.md §4).
    사용자가 그래도 기록하겠다고 하면, 앱은 **같은 `client_event_id` 를 그대로 두고**
    `confirm: true` 만 붙여 재전송해야 합니다 — 새 키를 쓰면 재시도가 두 줄이 됩니다.
    """
    try:
        event, created = await care_service.record(session, user.app_user_id, body)
    except PetNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _PET_NOT_FOUND) from None
    except care_service.MedicationConflictError as exc:
        # ⚠️ `HTTPException(detail=...)` 에 문자열이 아닌 dict 를 넣는 것은 이 저장소에서
        #    여기가 처음입니다. 다른 라우터는 전부 문자열만 씁니다 — 여기서는 사람 승인을
        #    받았습니다. 앱이 "아빠가 08:15에 줬어요" 를 그리려면 메시지 문장 하나로는
        #    부족하고 occurred_at·note·who 가 구조째로 필요하기 때문입니다.
        #
        #    `await` 는 리스트 컴프리헨션 안에서 못 쓰므로, 각 conflict 의 actor 이름을
        #    먼저 딕셔너리로 만들어 둔 뒤에 씁니다. **event id 가 아니라 actor 의
        #    user id 로 키를 잡습니다** — 같은 사람이 conflict 를 두 줄 남겼으면 event id
        #    로 잡을 때 그 사람만 두 번 묻게 됩니다. 사람 수만큼만 물어야 합니다.
        labels = await _actor_labels(
            session,
            await care_service.group_ids_for(session, user.app_user_id, body.pet_id),
            exc.conflicts,
        )
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "message": "이미 약을 챙긴 기록이 있습니다.",
                "conflicts": [
                    {
                        "id": str(e.id),
                        "occurred_at": e.occurred_at.isoformat(),
                        "note": e.note,
                        "actor": {
                            "app_user_id": str(e.actor_app_user_id)
                            if e.actor_app_user_id
                            else None,
                            "nickname": labels.get(e.actor_app_user_id)
                            if e.actor_app_user_id
                            else None,
                        },
                    }
                    for e in exc.conflicts
                ],
            },
        ) from None
    labels = await _actor_labels(
        session,
        await care_service.group_ids_for(session, user.app_user_id, event.pet_id),
        [event],
    )
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return _to_response(event, labels)


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
    labels = await _actor_labels(session, summary.group_ids, summary.events)
    # 산책 수행자도 **케어 actor 와 같은 규칙**입니다 — 지금도 그 아이의 구성원일 때만
    # 이름이 납니다. 사람 수만큼만 묻는 것도 같습니다 (`_actor_labels` 독스트링).
    walkers = {
        uid: await member_service.group_actor_label(session, summary.group_ids, uid)
        for uid in {w.app_user_id for w in summary.walk_rows}
    }
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
        events=[_to_response(e, labels) for e in summary.events],
        walk_rows=[
            DayWalkOut(
                walk_id=walk.id,
                started_at=walk.started_at,
                actor=ActorOut(
                    app_user_id=walk.app_user_id,
                    nickname=walkers.get(walk.app_user_id),
                ),
            )
            for walk in summary.walk_rows
        ],
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
