"""`/app/ai-cards/*` 응답 (#537). 카드 PNG 는 994×1582(5:8) 그대로 — 앱 표시 방식은 앱이 정한다."""

from __future__ import annotations

import datetime
import uuid
from typing import Literal

from pydantic import BaseModel


class AiCardResponse(BaseModel):
    id: uuid.UUID
    dog_id: uuid.UUID | None
    month: int
    dog_name: str
    title: str
    #: `generating` 이면 앱이 조금 뒤 다시 조회한다. `failed` 면 `error_code` 를 본다.
    status: Literal["generating", "ready", "failed"]
    error_code: str | None
    likeness: int | None
    attempts: int | None
    width: int | None
    height: int | None
    created_at: datetime.datetime
    #: 같은 요청에서 나온 장들을 묶는 값. 단일 생성 경로·옛 카드는 `null` (#572 Task 4).
    pick_group: uuid.UUID | None = None
    #: 이 요청에서 지금까지 `ready` 로 끝난 장수·**실제로 만들어진** 행 수(설정값이 아니다 —
    #: seed 가 모자란 달은 더 적을 수 있다). 단건 조회(POST 포함)에서만 채운다 — 목록에는
    #: 싣지 않는다(카드마다 따로 세면 N 번 두드리게 된다).
    done: int | None = None
    total: int | None = None
    #: ⚠️ **폴링은 `done == total` 이 아니라 이것으로 멈춘다** (#572 Task 4 fix round 1
    #: Important 1). "이 요청에 아직 만들어지는 중인 행이 하나도 없다" 는 뜻 — 카드가
    #: 실패해도(`done` 이 `total` 에 못 미쳐도) 더 나올 게 없으면 참이다.
    finished: bool | None = None
    #: 단건 조회에서 `ready` 일 때만 채운다. 목록에는 싣지 않는다.
    image_url: str | None = None


class AiCardListResponse(BaseModel):
    cards: list[AiCardResponse]
    #: 하루 한도와 오늘 남은 횟수 (#543, D-077). 무제한이면 둘 다 `null`. 앱이 「오늘 1번 남았어요」를 띄우고
    #: 0 이면 막는다 — 이 칸이 없는 옛 서버에서는 앱이 막지 않고 서버 429/409 문장에 맡긴다.
    daily_limit: int | None = None
    daily_remaining: int | None = None
