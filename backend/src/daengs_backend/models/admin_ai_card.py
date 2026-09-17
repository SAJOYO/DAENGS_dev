"""관리자 콘솔이 시험 삼아 뽑은 도감 카드 (#592, docs/cardimage/).

스키마 원본은 `db/init/41_admin_ai_cards.sql` 입니다. 이 모델은 그 SQL 을 따라가는 쪽이라,
SQL 을 고치면 여기도 손으로 맞춰야 합니다 (저장소 규칙).

`ai_cards`(앱 사용자가 만든 카드)와 **칸을 하나도 공유하지 않습니다** — 저쪽에 걸린 하루
한도 · 동시 생성 방어 · 탈퇴 정리 어느 것도 여기에는 없습니다. 콘솔 생성은 동기라 상태
칸(`status`)도 없고, 다 만들어진 카드만 들어오므로 이미지 칸이 전부 NOT NULL 입니다.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from daengs_backend.models.base import Base

#: 콘솔에서 고를 수 있는 엔진. 값은 `admin_ai_cards_engine` CHECK 과 같아야 합니다.
ADMIN_AI_CARD_ENGINES = ("gemini", "cardgen")


class AdminAiCard(Base):
    __tablename__ = "admin_ai_cards"

    __table_args__ = (
        CheckConstraint("engine IN ('gemini', 'cardgen')", name="admin_ai_cards_engine"),
        CheckConstraint("length(btrim(card_key)) > 0", name="admin_ai_cards_card_key"),
        CheckConstraint("likeness IS NULL OR likeness BETWEEN 1 AND 5", name="admin_ai_cards_likeness"),
        CheckConstraint("size_bytes > 0", name="admin_ai_cards_size"),
        #: 목록은 늘 "관리자 전원의 카드를 최근 것부터". **DESC 를 잃으면** 에러 없이
        #: 오래된 것부터 읽는 정렬이 되어 기본 50건이 엉뚱한 쪽을 줍니다.
        Index("idx_admin_ai_cards_created", text("created_at DESC")),
        #: `ai_cards` 와 달리 부분 인덱스가 아닙니다 — 이 칸이 NOT NULL 이라 거를 것이 없습니다.
        Index("idx_admin_ai_cards_storage_key", "storage_key", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    #: 누가 뽑았나. **목록을 가르는 칸이 아닙니다** — 콘솔 권한을 가진 관리자 전원이 서로 봅니다
    #: (사용자 결정 09-18).
    admin_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False,
    )

    #: 달이면 `"1"`~`"12"`, 종류면 `"strawberry"`·`"lettuce"` (`daengs_cardimage.catalog.card_key`).
    #: 정수가 아닙니다 — 달이 아닌 카드가 같은 칸에 들어옵니다.
    card_key: Mapped[str] = mapped_column(String(20))

    #: 카드에 **인쇄된** 이름과 제목. 나중에 무엇을 고쳐도 이 글자는 안 바뀝니다.
    dog_name: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(80))

    #: `gemini`(Nano Banana 2) · `cardgen`(FLUX.2-klein-4B).
    engine: Mapped[str] = mapped_column(String(20))

    #: GPU 경로에서만 씁니다. SmallInteger 가 아닙니다 — seed 는 32767 을 넘을 수 있습니다.
    seed: Mapped[int | None] = mapped_column(Integer)

    attempts: Mapped[int] = mapped_column(SmallInteger)

    #: 검수 결과. 검수를 끄고 뽑으면 둘 다 비어 있습니다.
    likeness: Mapped[int | None] = mapped_column(SmallInteger)
    judge_note: Mapped[str | None] = mapped_column(String(200))

    #: 저장 키는 `admin-ai-cards/{admin_user_id}/{card_id}.png`. 저장에 실패하면 행 자체를
    #: 안 만들므로 이 아래는 전부 채워집니다.
    storage_key: Mapped[str] = mapped_column(String(200))
    size_bytes: Mapped[int] = mapped_column(Integer)
    #: SmallInteger 가 아닙니다 — 앞으로 틀이 커져도 칸부터 고치는 일이 없게 둡니다.
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)

    #: 생성에 걸린 시간. 엔진을 서로 견줄 때 사람이 보는 숫자입니다.
    elapsed_ms: Mapped[int] = mapped_column(Integer)

    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=text("NOW()"))

    def __repr__(self) -> str:
        return f"<AdminAiCard {self.id} {self.card_key} {self.engine}>"
