"""AI 도감 카드 — 사진 한 장으로 서버가 만든 달 카드 (#537, D-076).

스키마 원본은 `db/init/38_ai_cards.sql` 입니다. 이 모델은 그 SQL 을 따라가는 쪽이라,
SQL 을 고치면 여기도 손으로 맞춰야 합니다 (저장소 규칙).

`dog_cards`(앱이 얼굴을 끼워 만든 카드, id 도 앱이 만듦)와 **별개입니다** — 이건 서버가
만든 카드 한 장 통째 PNG 라 id 도 서버가 만듭니다.
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

AI_CARD_STATUSES = ("generating", "ready", "failed")


class AiCard(Base):
    __tablename__ = "ai_cards"

    __table_args__ = (
        CheckConstraint("month BETWEEN 1 AND 12", name="ai_cards_month"),
        CheckConstraint("length(btrim(dog_name)) > 0", name="ai_cards_dog_name"),
        CheckConstraint("status IN ('generating', 'ready', 'failed')", name="ai_cards_status"),
        CheckConstraint(
            "status <> 'ready' OR (storage_key IS NOT NULL AND generation IS NOT NULL"
            " AND size_bytes IS NOT NULL AND width IS NOT NULL AND height IS NOT NULL)",
            name="ai_cards_ready_set",
        ),
        CheckConstraint("(status = 'failed') = (error_code IS NOT NULL)", name="ai_cards_failed_code"),
        CheckConstraint("size_bytes IS NULL OR size_bytes > 0", name="ai_cards_size"),
        CheckConstraint("likeness IS NULL OR likeness BETWEEN 1 AND 5", name="ai_cards_likeness"),
        CheckConstraint("attempts IS NULL OR attempts BETWEEN 1 AND 2", name="ai_cards_attempts"),
        Index("idx_ai_cards_owner_created", "app_user_id", "created_at"),
        Index(
            "idx_ai_cards_storage_key", "storage_key", unique=True,
            postgresql_where=text("storage_key IS NOT NULL"),
        ),
        #: 사용자별 동시 1장. 서비스가 add+commit 을 같은 try 로 감싸 IntegrityError → 409 로 바꿉니다.
        Index(
            "idx_ai_cards_one_generating", "app_user_id", unique=True,
            postgresql_where=text("status = 'generating'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    #: ⚠️ **이 CASCADE 에 기대면 안 됩니다.** 탈퇴는 `app_users` 행을 남깁니다 — 탈퇴 경로가 명시로 지웁니다.
    app_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("app_users.id", ondelete="CASCADE"))

    #: 어느 아이로 만들었나. **아이를 지워도 카드는 남습니다** (SET NULL).
    dog_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("pets.id", ondelete="SET NULL"))

    month: Mapped[int] = mapped_column(SmallInteger)
    #: 카드에 **인쇄된** 이름. 개명해도 안 바뀝니다.
    dog_name: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(80))

    #: `generating` → `ready` | `failed`. 되돌아가지 않습니다.
    status: Mapped[str] = mapped_column(String(16))
    #: `failed` 일 때만. `upstream`·`no_image`·`unavailable`·`storage`·`interrupted`·`internal`.
    error_code: Mapped[str | None] = mapped_column(String(32))

    storage_key: Mapped[str | None] = mapped_column(String(200))
    # local 저장소는 64자 hex sha256 을 쓰고 `ai_cards_ready_set` 이 이 칸을 요구합니다 —
    # 더 긴 generation 을 쓰는 저장소는 칸부터 넓히세요.
    generation: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(SmallInteger)
    height: Mapped[int | None] = mapped_column(SmallInteger)
    likeness: Mapped[int | None] = mapped_column(SmallInteger)
    attempts: Mapped[int | None] = mapped_column(SmallInteger)

    #: 이 카드를 만든 seed. 같은 seed 가 같은 자리를 깨뜨리므로(#557 E1) 기록해 둔다.
    #: 09-15 에 `PETL PPAUSE` 3건이 전부 같은 seed 였는데 기록이 없어 나중에야 알았다.
    #: SmallInteger 가 아니다 — seed 는 32767 을 넘을 수 있다.
    seed: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=text("NOW()"))
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=text("NOW()"))

    def __repr__(self) -> str:
        return f"<AiCard {self.id} {self.status}>"


class AiCardUsage(Base):
    """AI 카드 하루 한도를 세는 사용 기록 (#543, D-077). 스키마 원본은 `db/init/39_ai_card_usage.sql`.

    카드가 `ready` 가 되는 순간 한 줄. **카드를 지워도 남습니다** — 그래서 `card_id` 에 FK 가 없습니다.
    """

    __tablename__ = "ai_card_usage"

    __table_args__ = (Index("idx_ai_card_usage_owner_used", "app_user_id", "used_at"),)

    card_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)

    #: ⚠️ **이 CASCADE 에 기대면 안 됩니다.** 탈퇴는 `app_users` 행을 남깁니다 — `cleanup_for_owner` 가 지웁니다.
    app_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("app_users.id", ondelete="CASCADE"))

    used_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=text("NOW()"))

    def __repr__(self) -> str:
        return f"<AiCardUsage {self.card_id} {self.used_at}>"
