"""앱 회원의 강아지. 원본 스키마는 `db/init/05_pets.sql` 입니다.

`app_users` 와 달리 **암호화 컬럼이 없습니다.** 강아지 이름·견종·몸무게는 사람을
식별하는 값이 아니라서입니다 — 그 판단의 근거는 SQL 쪽 주석에 적어 두었습니다.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
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

PET_SEXES = ("male", "female")

#: 생일 칸에 든 날짜가 무슨 날인지.
#:
#: 태어난 날을 모르면 가족이 된 날로 대신 받는데, **어느 쪽인지를 같이 두어야**
#: "3살이에요"와 "함께한 지 2년이에요"를 구분할 수 있습니다.
PET_BIRTH_DATE_KINDS = ("birthday", "family_day")


class Pet(Base):
    __tablename__ = "pets"

    __table_args__ = (
        CheckConstraint("sex IN ('male','female')", name="pets_sex_check"),
        CheckConstraint(
            "birth_date_kind IN ('birthday','family_day')",
            name="pets_birth_date_kind_check",
        ),
        # 날짜와 종류는 같이 있거나 같이 없어야 합니다.
        CheckConstraint(
            "(birth_date IS NULL) = (birth_date_kind IS NULL)",
            name="pets_birth_date_pair",
        ),
        CheckConstraint(
            "weight_kg > 0 AND weight_kg <= 200", name="pets_weight_kg_check"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )

    # 계정이 지워지면 강아지도 같이 지워집니다. 탈퇴가 개인정보를 파기하는데
    # 강아지가 남으면 파기가 반쪽입니다.
    app_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app_users.id", ondelete="CASCADE")
    )

    name: Mapped[str] = mapped_column(String(40))

    # 앱이 정하는 어휘라 CHECK 를 걸지 않습니다 (SQL 주석 참고).
    breed: Mapped[str] = mapped_column(String(60))

    # None 은 '모름'입니다. **모름을 false 로 바꾸지 않습니다** —
    # 안 물어본 것과 아니라고 답한 것은 다릅니다.
    sex: Mapped[str | None] = mapped_column(String(10))
    neutered: Mapped[bool | None] = mapped_column(Boolean)

    weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))

    birth_date: Mapped[date | None] = mapped_column(Date)
    birth_date_kind: Mapped[str | None] = mapped_column(String(20))

    #: 배웅한 날. **NULL 이면 아직 함께 있는 아이입니다.**
    #:
    #: 삭제와 다른 일이라 칸을 따로 둡니다 — 목록에서 지우는 것은 없던 일로 만드는
    #: 것이고, 배웅은 있었던 일을 적어 두는 것입니다. 행을 안 지우고 이 날짜만 채웁니다.
    farewell_on: Mapped[date | None] = mapped_column(Date)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )

    def __repr__(self) -> str:
        return f"<Pet {self.name} breed={self.breed}>"
