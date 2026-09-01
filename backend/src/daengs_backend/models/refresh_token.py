"""재발급 토큰. 원본 스키마는 `db/init/03_auth.sql` 입니다.

관리자와 앱 회원의 세션이 **한 테이블에 같이** 들어갑니다 (D-016).
소유자 컬럼이 둘이고 **정확히 하나만 채워집니다** — DB 의 CHECK 제약이 지킵니다.
어느 쪽인지 읽을 때는 컬럼을 직접 보지 말고 `subject_type` / `subject_id` 를 쓰세요.
"""

import uuid
from datetime import datetime

from sqlalchemy import CHAR, DateTime, ForeignKey, Text, Uuid, text
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from daengs_backend.core.subject import SubjectType
from daengs_backend.models.base import Base


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )

    # 소유자는 **둘 중 하나만** 채워집니다 (CHECK 제약).
    # nullable 인 것은 "없어도 된다"가 아니라 "다른 쪽이 채워졌다"는 뜻입니다.
    # FK 를 둘로 나눈 이유는 subject_type + subject_id 한 쌍으로 하면 FK 무결성을
    # 포기하게 되기 때문입니다 (D-016, 03_auth.sql 주석).
    admin_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("admin_users.id", ondelete="CASCADE")
    )
    app_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("app_users.id", ondelete="CASCADE")
    )

    # 토큰 원문의 SHA-256 hex 64자. 원문은 어디에도 저장하지 않습니다.
    token_hash: Mapped[str] = mapped_column(CHAR(64), unique=True)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # NULL 이면 살아 있는 세션입니다.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # 표시용입니다. 인증 판단에 쓰지 마세요 (클라이언트가 바꿀 수 있는 값입니다).
    user_agent: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(INET)

    # created_at 이 곧 발급 시각입니다.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )

    # AdminUser / AppUser 와의 relationship 은 일부러 두지 않았습니다.
    # async 세션에서 lazy load 가 걸리면 MissingGreenlet 으로 터지고,
    # 지금 필요한 조회는 전부 소유자 컬럼으로 직접 거는 것뿐입니다.
    # 조인이 필요해지면 그때 selectin/joined 로딩을 명시해서 추가하세요.

    @property
    def subject_type(self) -> SubjectType:
        """이 세션의 주인이 관리자인지 앱 회원인지.

        **컬럼을 직접 보고 분기하지 마세요.** `if row.admin_user_id:` 로 쓰면
        앱 회원 행에서 조용히 False 로 빠져 "관리자가 아니다"가 아니라
        "값이 없다"로 뭉개집니다.
        """
        if self.admin_user_id is not None:
            return SubjectType.ADMIN
        if self.app_user_id is not None:
            return SubjectType.APP
        # CHECK 제약이 막고 있습니다. 여기까지 왔다면 제약 없이 만들어진 행입니다.
        raise ValueError(f"소유자가 없는 refresh 행입니다 (id={self.id}).")

    @property
    def subject_id(self) -> uuid.UUID:
        """주인의 UUID. **어느 테이블인지는 subject_type 이 정합니다.**"""
        owner = self.admin_user_id or self.app_user_id
        if owner is None:
            raise ValueError(f"소유자가 없는 refresh 행입니다 (id={self.id}).")
        return owner

    def __repr__(self) -> str:
        # token_hash 도 넣지 않습니다. 원문은 아니지만 로그에 남길 이유가 없습니다.
        state = "revoked" if self.revoked_at else "active"
        return f"<RefreshToken {self.subject_type.value}={self.subject_id} {state}>"
