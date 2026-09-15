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
    #: 단건 조회에서 `ready` 일 때만 채운다. 목록에는 싣지 않는다.
    image_url: str | None = None


class AiCardListResponse(BaseModel):
    cards: list[AiCardResponse]
