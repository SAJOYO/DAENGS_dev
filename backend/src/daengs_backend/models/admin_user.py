"""관리자 콘솔 계정. 원본 스키마는 `db/init/03_auth.sql` 입니다."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from daengs_backend.models.base import Base

# CHECK 제약과 같은 값입니다. SQL 쪽을 고치면 여기도 고쳐야 합니다.
# 애플리케이션에서 role 을 비교할 때 문자열 리터럴을 흩뿌리지 않으려고 둡니다.
ADMIN_ROLES = ("ADMIN", "OPERATOR", "CURATOR", "ANALYST", "VIEWER")
ADMIN_STATUSES = ("active", "suspended")


class AdminUser(Base):
    __tablename__ = "admin_users"

    # 모델은 SQL 을 따라가는 쪽이라 제약을 여기서 만들지는 않습니다
    # (create_all 을 부르지 않으므로 DDL 로 나가지 않습니다).
    # 그래도 적어 두면 이 파일만 보고도 허용값을 알 수 있습니다.
    __table_args__ = (
        CheckConstraint(
            "role IN ('ADMIN','OPERATOR','CURATOR','ANALYST','VIEWER')",
            name="admin_users_role_check",
        ),
        CheckConstraint(
            "status IN ('active','suspended')",
            name="admin_users_status_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        # 기본값 생성은 DB 에 맡깁니다 (파이썬에서 만들면 SQL 쪽 DEFAULT 와 둘로 갈립니다).
        server_default=text("gen_random_uuid()"),
    )

    login_id: Mapped[str] = mapped_column(String(50), unique=True)

    # Argon2id PHC 문자열. 이 값이 응답에 실려 나가지 않게 schemas/ 에서 걸러야 합니다.
    password_hash: Mapped[str] = mapped_column(Text)

    name: Mapped[str] = mapped_column(String(50))

    role: Mapped[str] = mapped_column(String(20))

    status: Mapped[str] = mapped_column(String(20), server_default=text("'active'"))

    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )
    # UPDATE 때 갱신하는 것은 DB 트리거(set_updated_at)입니다.
    # onupdate= 를 여기 걸면 파이썬이 계산한 값이 트리거에 다시 덮여, 같은 일을 두 번 합니다.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )

    def __repr__(self) -> str:
        # password_hash 는 절대 넣지 않습니다. repr 은 로그와 예외 메시지에 그대로 찍힙니다.
        return f"<AdminUser {self.login_id} role={self.role} status={self.status}>"
