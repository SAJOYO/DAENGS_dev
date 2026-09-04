"""도감 카드 — 앱이 뽑아 둔 것의 서버 사본 (D-052).

스키마 원본은 `db/init/10_dog_cards.sql` 입니다. 이 모델은 그 SQL 을 따라가는 쪽이라,
SQL 을 고치면 여기도 손으로 맞춰야 합니다 (저장소 규칙).

⚠️ **id 를 서버가 만들지 않습니다.** 다른 도메인은 backend 가 키를 만들지만(원칙 6)
   카드는 예외입니다 — **오프라인에서 먼저 만들어집니다.** 로그인 없이 둘러보기로도
   뽑고, 앱의 Room 에 이미 그 id 로 들어가 있습니다. 앱이 그렇게 설계해 뒀습니다
   ("서버가 붙어도 이 id 를 그대로 올려서 **재전송이 멱등해진다**").
   그래서 쓰기는 POST 가 아니라 **PUT upsert** 입니다.

   원칙 6 이 막으려던 것(남의 경로를 덮어쓰기)은 여기서 **소유자 조건**이 막습니다 —
   남이 이미 가진 카드 id 로 PUT 하면 409 입니다.

⚠️ **결과를 저장하고 시드를 저장하지 않습니다.** 카드는 "그때 그 사진 + 그때 뽑힌
   야채" 라 다시 만들 수가 없습니다. 확률표를 고쳤다고 이미 가진 카드가 바뀌면 안 됩니다.
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
    String,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from daengs_backend.models.base import Base


class DogCard(Base):
    __tablename__ = "dog_cards"

    __table_args__ = (
        # 사각형이 뒤집히면 그리는 쪽에서 조용히 이상해집니다. 여기서 막습니다.
        CheckConstraint(
            "core_right > core_left AND core_bottom > core_top",
            name="dog_cards_core_rect",
        ),
        CheckConstraint(
            "(face_storage_key IS NULL) = (face_generation IS NULL)"
            " AND (face_storage_key IS NULL) = (face_size_bytes IS NULL)",
            name="dog_cards_face_set",
        ),
        CheckConstraint(
            "face_size_bytes IS NULL OR face_size_bytes > 0",
            name="dog_cards_face_size_check",
        ),
        Index("idx_dog_cards_owner_drawn", "app_user_id", "drawn_at"),
        Index("idx_dog_cards_owner_template", "app_user_id", "template_id"),
    )

    #: **앱이 만든 UUID.** 서버가 기본값을 안 겁니다 — 걸면 앱이 안 보낸 경우 새 id 가
    #: 생겨 같은 카드가 두 장이 됩니다.
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)

    #: ⚠️ **이 CASCADE 에 기대면 안 됩니다.** 탈퇴는 `app_users` 행을 남기므로
    #:    영영 안 돕니다 — 탈퇴 경로가 명시로 지웁니다 (chats·screening 과 같습니다).
    app_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app_users.id", ondelete="CASCADE")
    )

    #: 어느 야채인가. **번호가 아니라 문자열**입니다 — 목록의 원본이 저쪽 저장소라,
    #: 저쪽이 순서를 바꾸면 번호가 밀려 어제 뽑은 배추가 오늘 피망이 됩니다.
    template_id: Mapped[str] = mapped_column(String(80))

    #: 어느 아이로 뽑았나. **아이를 지워도 카드는 남습니다** (FK 가 SET NULL).
    dog_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("pets.id", ondelete="SET NULL")
    )

    #: 카드에 **인쇄된** 이름. `dog_id` 와 성질이 다릅니다 — 그때 찍힌 글자라
    #: **개명해도 안 바뀝니다.**
    dog_name: Mapped[str] = mapped_column(String(40))

    drawn_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True))

    #: 번호판 글자. 생일에서 만듭니다.
    code_text: Mapped[str] = mapped_column(String(40), server_default=text("''"))

    #: **사용자가 원형 틀에 직접 맞춘 카드인가.** false 인 옛 카드는 예전 규칙 그대로
    #: 그립니다 — 이미 뽑아 둔 카드가 업데이트로 달라지면 안 됩니다.
    user_framed: Mapped[bool] = mapped_column(Boolean, server_default=text("FALSE"))

    #: 또렷한 얼굴만의 자리. **없으면 다음에 열 때 얼굴이 밀립니다.**
    core_left: Mapped[int] = mapped_column(Integer)
    core_top: Mapped[int] = mapped_column(Integer)
    core_right: Mapped[int] = mapped_column(Integer)
    core_bottom: Mapped[int] = mapped_column(Integer)

    #: 누끼 딴 얼굴 PNG. **원본 사진은 저장하지 않습니다** — 배경이 통째로 날아가서
    #: 뒤에 찍힌 집도 사람도 같이 지워집니다. 원본보다 안전합니다.
    #: None 이면 아직 안 올라온 것이고, 그때 앱은 자기 기기의 파일을 씁니다.
    face_storage_key: Mapped[str | None] = mapped_column(String(200))
    face_generation: Mapped[str | None] = mapped_column(String(64))
    face_size_bytes: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )

    def __repr__(self) -> str:
        return f"<DogCard {self.id} template={self.template_id}>"
