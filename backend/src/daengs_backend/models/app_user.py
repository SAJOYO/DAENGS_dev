"""앱 회원 (카카오 소셜 로그인). 원본 스키마는 `db/init/03_auth.sql` 입니다.

`*_enc` 는 AES-256-GCM 암호문이고 `email_hash` 는 HMAC 기반 blind index 입니다.
**이 모델은 암복호화를 하지 않습니다.** 넣고 꺼내는 것은 바이트열 그대로이고,
변환은 services 계층에서 `core/crypto.py`(짝 카드)를 불러서 합니다.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    LargeBinary,
    String,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from daengs_backend.models.base import Base

APP_USER_STATUSES = ("active", "suspended", "withdrawn")


class AppUser(Base):
    __tablename__ = "app_users"

    __table_args__ = (
        CheckConstraint(
            "status IN ('active','suspended','withdrawn')",
            name="app_users_status_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )

    # 평문입니다. 로그인 조인 키라서 암호화하지 않습니다 (03_auth.sql 주석 참고).
    kakao_id: Mapped[int] = mapped_column(BigInteger, unique=True)

    # 검색은 email_hash 로, 표시는 email_enc 를 복호화해서 합니다.
    # email_enc 로는 WHERE 를 걸 수 없습니다 - 같은 값이라도 암호문이 매번 다릅니다.
    email_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    email_hash: Mapped[str | None] = mapped_column(CHAR(64), unique=True)

    # 검색용 해시가 없습니다. 조회 조건으로 쓸 수 없습니다.
    phone_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    name_enc: Mapped[bytes | None] = mapped_column(LargeBinary)

    status: Mapped[str] = mapped_column(String(20), server_default=text("'active'"))

    # 대표 강아지. 상단바·챗봇 얼굴이 이 아이를 따릅니다.
    #
    # **pets 쪽에 is_primary 를 두지 않은 이유**는 05_pets.sql 에 적어 두었습니다 —
    # 요약하면 계정에 한 칸을 두어야 "한 마리만 대표"를 DB 가 저절로 보장합니다.
    # 그 강아지가 지워지면 NULL 이 되고, 승계는 서비스 계층이 합니다.
    primary_pet_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("pets.id", ondelete="SET NULL")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )

    def __repr__(self) -> str:
        # 암호문이라도 repr 로 흘리지 않습니다.
        return f"<AppUser kakao_id={self.kakao_id} status={self.status}>"
