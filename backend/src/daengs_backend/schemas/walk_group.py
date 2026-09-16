"""산책 기록 공동 조회 응답. **필드를 늘릴 때 게임·점령 값을 넣지 마세요** — 이번 결정으로
후순위 보류이고, 키 집합은 `tests/test_walk_group_reads.py` 가 고정합니다."""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class GroupWalkActor(BaseModel):
    """누가 다녀왔나. `nickname` 은 지금도 그 그룹의 구성원일 때만 옵니다 — 나간 사람은 None."""

    app_user_id: uuid.UUID
    nickname: str | None


class GroupWalkItem(BaseModel):
    id: uuid.UUID
    started_at: datetime
    ended_at: datetime
    #: `ended_at - started_at`. 늘 있습니다.
    duration_s: int
    #: 봉인된 최신 계산 세대의 이동 거리·이동 시간. 아직 계산 전이면 None.
    distance_m: int | None
    moving_s: int | None
    actor: GroupWalkActor
    is_mine: bool
    #: 요청자가 볼 수 있는 강아지만.
    pet_ids: list[uuid.UUID]


class GroupWalkPoint(BaseModel):
    at: datetime
    lat: Decimal
    lng: Decimal


class GroupWalkDetail(GroupWalkItem):
    points: list[GroupWalkPoint]


class GroupWalkListResponse(BaseModel):
    pet_id: uuid.UUID
    walks: list[GroupWalkItem]
    #: 다음 페이지가 있으면 그대로 `cursor` 로 넘깁니다. 마지막 페이지면 None.
    next_cursor: str | None


__all__ = ["GroupWalkActor", "GroupWalkDetail", "GroupWalkItem", "GroupWalkListResponse", "GroupWalkPoint"]
