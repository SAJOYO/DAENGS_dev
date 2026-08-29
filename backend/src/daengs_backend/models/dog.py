"""반려견 프로필. 원본 스키마는 `db/init/04_dogs.sql` 입니다.

DAENGS 의 canonical Dog Profile — Place 검색 게이트웨이가 여기서 size/weight/age 를
projection 하고, 에이전트의 "프로필 + 진료 이력 주입"도 장차 같은 행을 읽습니다.
Place 요구에 스키마를 역으로 맞추지 않습니다.

암호화 컬럼이 없습니다 — D-012 의 대상은 사람의 개인정보이고, 개의 이름·생일은
그 축이 아니라고 봤습니다 (04_dogs.sql 주석). 판단이 바뀌면 app_users 처럼 `*_enc` 로.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from daengs_backend.models.base import Base

DOG_SIZE_CLASSES = ("small", "medium", "large")
DOG_SEXES = ("M", "F")


class Dog(Base):
    __tablename__ = "dogs"

    __table_args__ = (
        CheckConstraint("sex IN ('M','F')", name="dogs_sex_check"),
        CheckConstraint(
            "weight_kg > 0 AND weight_kg <= 200", name="dogs_weight_kg_check"
        ),
        CheckConstraint(
            "size_class IN ('small','medium','large')", name="dogs_size_class_check"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )

    # 소유자. 탈퇴는 행 삭제가 아니라 app_users.status 로 표현되므로 CASCADE 없음.
    app_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app_users.id"), index=True
    )

    name: Mapped[str] = mapped_column(String(50))

    # 자유 입력. 품종 사전을 강제하지 않습니다 — 믹스·미상이 흔합니다.
    breed: Mapped[str | None] = mapped_column(String(100))

    # 나이는 저장하지 않습니다. 저장하는 순간부터 낡습니다 — 소비자가 여기서 계산합니다.
    birth_date: Mapped[date | None] = mapped_column(Date)

    sex: Mapped[str | None] = mapped_column(CHAR(1))
    neutered: Mapped[bool | None] = mapped_column(Boolean)

    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))

    # 무게에서 파생하지 않고 따로 받습니다 — 크기 등급은 무게의 함수가 아니라
    # 보호자가 아는 사실입니다. 같은 9kg 도 견종에 따라 small/medium 이 갈립니다.
    size_class: Mapped[str] = mapped_column(String(10))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )

    def __repr__(self) -> str:
        return f"<Dog id={self.id} size_class={self.size_class}>"
