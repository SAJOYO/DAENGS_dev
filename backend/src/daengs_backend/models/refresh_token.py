"""관리자 재발급 토큰. 원본 스키마는 `db/init/03_auth.sql` 입니다."""

import uuid
from datetime import datetime

from sqlalchemy import CHAR, DateTime, ForeignKey, Text, Uuid, text
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from daengs_backend.models.base import Base


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )

    admin_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("admin_users.id", ondelete="CASCADE")
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

    # AdminUser 와의 relationship 은 일부러 두지 않았습니다.
    # async 세션에서 lazy load 가 걸리면 MissingGreenlet 으로 터지고,
    # 지금 필요한 조회는 전부 admin_user_id 로 직접 거는 것뿐입니다.
    # 조인이 필요해지면 그때 selectin/joined 로딩을 명시해서 추가하세요.

    def __repr__(self) -> str:
        # token_hash 도 넣지 않습니다. 원문은 아니지만 로그에 남길 이유가 없습니다.
        state = "revoked" if self.revoked_at else "active"
        return f"<RefreshToken admin={self.admin_user_id} {state}>"
