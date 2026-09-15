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


class GroupWalkPreviewPoint(BaseModel):
    """카드 썸네일용 좌표 하나. 시각이 없고 소수 6자리로 줄였습니다 — 상세 경로가 아닙니다."""

    lat: float
    lng: float


class GroupWalkFeedItem(GroupWalkItem):
    """통합 산책 목록(`GET /app/pet-walks`)의 한 건. #539 항목에 카드가 그릴 값만 더합니다.

    ⚠️ 여기의 `pet_ids` 는 **요청자 화면의 강아지 id**(`/app/pets` 의 카드 id)입니다 — 연결된 그룹에서
    남의 행에 태그된 산책도 내 카드 id 로 바꿔 줍니다. #539 목록의 `pet_ids` 는 행 id 그대로입니다.
    """

    weather_code: int | None
    is_day: bool | None
    temperature_c: Decimal | None
    #: 이어 붙이면 안 걸은 길이 생기므로 `chain_index` 마다 따로. 좌표가 없으면 빈 목록.
    route_preview: list[list[GroupWalkPreviewPoint]]


class GroupWalkCarer(BaseModel):
    """보호자 조건 후보. **지금** 그 강아지의 구성원(대표 ∪ 돌보미)만."""

    app_user_id: uuid.UUID
    nickname: str | None
    is_me: bool
    #: 이 사람이 함께 돌보는 요청자 화면의 강아지 id.
    pet_ids: list[uuid.UUID]


class GroupWalkTotals(BaseModel):
    """페이지가 아니라 **조건 전체**의 합계."""

    count: int
    #: 최신 계산 세대의 이동 거리 합. 계산 전 산책은 0 으로 셉니다.
    distance_m: int
    #: `ended_at - started_at` 의 합.
    duration_s: int


class GroupWalkFeedResponse(BaseModel):
    walks: list[GroupWalkFeedItem]
    totals: GroupWalkTotals
    carers: list[GroupWalkCarer]
    next_cursor: str | None


__all__ = [
    "GroupWalkActor",
    "GroupWalkCarer",
    "GroupWalkDetail",
    "GroupWalkFeedItem",
    "GroupWalkFeedResponse",
    "GroupWalkItem",
    "GroupWalkListResponse",
    "GroupWalkPoint",
    "GroupWalkPreviewPoint",
    "GroupWalkTotals",
]
