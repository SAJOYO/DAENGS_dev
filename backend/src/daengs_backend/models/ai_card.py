"""AI 도감 카드 — 사진 한 장으로 서버가 만든 카드 (#537, D-076 · D-085).

달(1~12)만이 아닙니다 — 종류 카드(딸기·상추)도 같은 표에 들어옵니다 (#593, D-085).
무엇을 만들었는지는 `card_key` 가 갖고, `month` 는 달 카드에만 있습니다.

스키마 원본은 `db/init/38_ai_cards.sql` 입니다. 이 모델은 그 SQL 을 따라가는 쪽이라,
SQL 을 고치면 여기도 손으로 맞춰야 합니다 (저장소 규칙).

`dog_cards`(앱이 얼굴을 끼워 만든 카드, id 도 앱이 만듦)와 **별개입니다** — 이건 서버가
만든 카드 한 장 통째 PNG 라 id 도 서버가 만듭니다.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    Boolean,
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
        #: 달 카드와 종류 카드를 한 표에 담는 규칙 (D-085). 달이 있으면 1~12 이고 `card_key` 가
        #: 그 달의 문자열이어야 하며, 달이 없으면(종류 카드) `card_key` 는 숫자가 아니어야 한다 —
        #: 숫자를 막지 않으면 「month 는 비었는데 card_key 가 '4'」 인 행이 서고, 그건 앱에
        #: `month=null` 로 나가는 달 카드다(전환기 계약이 `month` 를 함께 싣는다).
        CheckConstraint(
            "(month IS NOT NULL AND month BETWEEN 1 AND 12 AND card_key = month::text)"
            " OR (month IS NULL AND card_key !~ '^[0-9]+$')",
            name="ai_cards_month",
        ),
        CheckConstraint("length(btrim(card_key)) > 0", name="ai_cards_card_key"),
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
        #: 사용자별 동시 **요청** 1개(행 1개가 아닙니다 — #572 Task 4 fix round 1 Critical).
        #: 한 요청의 행은 전부 `pick_group` 을 공유하고, 그 대표 행(`id = pick_group`)만 이
        #: 인덱스가 봅니다 — 그래야 같은 요청의 형제 행 여럿이 동시에 `generating` 이어도
        #: 걸리지 않으면서, 다른(진짜 동시) 요청은 여전히 막습니다. 서비스가 add+commit 을
        #: 같은 try 로 감싸 IntegrityError → 409 로 바꿉니다.
        Index(
            "idx_ai_cards_one_generating", "app_user_id", unique=True,
            postgresql_where=text("status = 'generating' AND id = pick_group"),
        ),
        #: 「고른 카드만 남기고 형제를 지운다」 가 pick_group 으로 형제를 찾을 때 쓴다.
        Index("ix_ai_cards_pick_group", "pick_group"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    #: ⚠️ **이 CASCADE 에 기대면 안 됩니다.** 탈퇴는 `app_users` 행을 남깁니다 — 탈퇴 경로가 명시로 지웁니다.
    app_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("app_users.id", ondelete="CASCADE"))

    #: 어느 아이로 만들었나. **아이를 지워도 카드는 남습니다** (SET NULL).
    dog_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("pets.id", ondelete="SET NULL"))

    #: 달 카드의 달(1~12). **종류 카드(딸기·상추)는 `None`** 입니다 — 그 카드에는 달이 없습니다 (D-085).
    month: Mapped[int | None] = mapped_column(SmallInteger)
    #: 무엇을 만들었나. 달이면 `"4"`, 종류면 `"strawberry"`·`"lettuce"`
    #: (`daengs_cardimage.catalog.card_key`). 한도(강아지당 카드 종류 1장)가 이 값을 셉니다.
    card_key: Mapped[str] = mapped_column(String(20))
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

    #: 같은 요청에서 나온 장들을 묶는다. 사용자가 하나를 고르면 나머지 형제 행은 지운다 (#572 Task 4).
    #: **요청의 대표(첫) 행은 자기 `id` 를 그대로 쓴다** (`pick_group == id`) — `idx_ai_cards_one_
    #: generating` 이 그 한 행만 보고 「사용자별 동시 1장」을 지키게 하기 위해서다(fix round 1
    #: Critical). 그래서 카드가 한 장뿐이어도 `pick_group` 은 항상 채워진다 — `NULL` 은 이
    #: 기능이 생기기 전(마이그레이션 이전)의 옛 행에만 남는다.
    pick_group: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=text("NOW()"))
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=text("NOW()"))

    def __repr__(self) -> str:
        return f"<AiCard {self.id} {self.status}>"


class AiCardUsage(Base):
    """AI 카드 한도를 세는 기록 (#543 · #572, D-077 · D-084). 스키마 원본은 `db/init/39_ai_card_usage.sql`.

    **요청(`pick_group`) 하나에 한 줄.** 요청의 첫 슬롯을 잡을 때(유료 호출 전) 시도 표시
    (`unfulfilled_attempt=True`, 돈 나간 시도 상한이 셈)를 남기고, 닮음이 기준(`cardimage_judge_min`)
    이상인 카드가 처음 `ready` 가 되면 그것을 사용 기록(`unfulfilled_attempt=False`, 하루 한도가 셈)으로
    바꿉니다 — 규칙은 `services/ai_card_quota.py`. **카드를 지워도 남습니다** — 그래서 `card_id` 에
    FK 가 없습니다.
    """

    __tablename__ = "ai_card_usage"

    __table_args__ = (Index("idx_ai_card_usage_owner_used", "app_user_id", "used_at"),)

    card_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)

    #: ⚠️ **이 CASCADE 에 기대면 안 됩니다.** 탈퇴는 `app_users` 행을 남깁니다 — `cleanup_for_owner` 가 지웁니다.
    app_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("app_users.id", ondelete="CASCADE"))

    used_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=text("NOW()"))

    #: `True` 면 사용 기록이 아니라 **유료 호출까지 갔는데 아직 좋은 카드가 안 나온 요청**의 표시다
    #: (#572, D-084) — 생성 중·닮음 미달·호출 실패·생성 중 삭제 모두. 그때 `card_id` 는 그 요청의
    #: `pick_group`(대표 행 id)이다 — 같은 요청에서 기준 이상 카드가 나오면 `_finish_ready` 가 그 값으로
    #: 이 줄을 찾아 지운다.
    unfulfilled_attempt: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))

    def __repr__(self) -> str:
        return f"<AiCardUsage {self.card_id} {self.used_at} unfulfilled_attempt={self.unfulfilled_attempt}>"
