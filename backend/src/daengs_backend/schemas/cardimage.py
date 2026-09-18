"""`/admin/cardimage/*` — 콘솔의 「도감 카드 생성」 탭이 주고받는 모양 (#496, #592).

앱 스키마(`schemas/ai_card.py`)와 **아무것도 공유하지 않습니다** — 이쪽은 관리자가 엔진을
견주어 보는 화면이라 `seed`·`engine` 처럼 앱에 안 나가는 값을 싣습니다.
"""

from __future__ import annotations

import datetime
import uuid

from pydantic import BaseModel, Field


class JudgeOut(BaseModel):
    likeness: int = Field(ge=1, le=5)
    text_ok: bool
    avatar_ok: bool
    note: str


class CardImageResponse(BaseModel):
    """방금 뽑은 카드 한 장.

    `month` 대신 `card` 입니다 (#592) — 달이 아닌 카드(딸기·상추)가 같은 경로로 나오면서
    정수로는 무엇을 만들었는지 말할 수 없게 됐습니다. 콘솔 화면이 유일한 소비자라
    (`frontend/app/components/cardimage-inspect.tsx`) 옛 칸을 남기지 않습니다.
    """

    #: 저장된 행의 id. **저장소가 꺼져 있으면 `None`** 이고 그때 `stored` 도 거짓입니다.
    id: uuid.UUID | None
    #: 무엇을 만들었나 — 달은 `"4"`, 종류는 `"strawberry"` (`daengs_cardimage.catalog.card_key`).
    card: str
    title: str
    attempts: int
    judge: JudgeOut | None
    png_base64: str
    elapsed_ms: int
    #: 실제로 부른 엔진 이름(`gemini`·`cardgen`).
    engine: str
    #: 엔진에 넘어간 seed. **Nano Banana 2 는 seed 를 버리므로 `None`** 입니다 — 기록해 두면
    #: 나중에 그 값으로 같은 장을 다시 뽑을 수 있다는 거짓말이 됩니다(`services/ai_card.py::_finish_ready` 와 같은 규칙).
    seed: int | None
    #: 카드가 표에 남았나. 거짓이면 이 응답의 PNG 가 유일한 사본입니다 (spec ④).
    stored: bool


class AdminCardOut(BaseModel):
    """저장된 카드 한 줄. **이미지 바이트는 안 싣습니다** — `GET /cards/{id}/image` 로 따로 받습니다."""

    id: uuid.UUID
    admin_user_id: uuid.UUID
    card: str
    dog_name: str
    title: str
    engine: str
    seed: int | None
    attempts: int
    likeness: int | None
    judge_note: str | None
    width: int
    height: int
    size_bytes: int
    elapsed_ms: int
    created_at: datetime.datetime


class AdminCardListResponse(BaseModel):
    cards: list[AdminCardOut]


class CardOption(BaseModel):
    #: 그대로 `POST /generate` 의 `card` 에 넣는 값 — 달은 `"4"`, 종류는 `"strawberry"`.
    key: str
    label: str


class EngineOption(BaseModel):
    key: str
    label: str
    #: 지금 이 서버에서 부를 수 있나. 거짓이면 화면이 비활성으로 두고 `reason` 을 띄웁니다.
    available: bool
    reason: str | None = None


class CardImageOptions(BaseModel):
    """콘솔이 화면을 그리기 전에 한 번 받는 것 (#592 spec ⑥).

    카드 목록과 사진 안내를 프론트에 복제해 두면 둘이 갈라집니다 — 실제로 `MONTHS` 는 4·9월에
    멈춰 있었고 `PHOTO_GUIDANCE` 는 손으로 맞춘 사본이었습니다.
    """

    cards: list[CardOption]
    engines: list[EngineOption]
    photo_guidance: str
