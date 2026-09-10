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
        CheckConstraint(
            "(ocr_consent_at IS NULL) = (ocr_consent_version IS NULL)",
            name="app_users_ocr_consent_pair",
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

    # 미니룸 앞에 걸리는 이름표. 사용자가 직접 정합니다.
    #
    # **None 은 "아직 안 정했다"** 이고, 그때 앱이 대표 강아지 이름으로 짓습니다.
    # 빈 문자열로 두지 않습니다 — "정해서 지웠다" 와 구분이 안 됩니다.
    room_name: Mapped[str | None] = mapped_column(String(20))

    # 사람을 가리키는 이름. **room_name 과 다릅니다** — 저건 집 이름이고("네옹이네")
    # 이건 그 집 사람 이름입니다. 앱의 `RoomLabel.kt` 가 room_name 을 가구 이름으로
    # 만들기 때문에 하나로 겸할 수 없습니다.
    #
    # **서버가 발급합니다** (`services/app_auth.py` 의 `_ensure_nickname`).
    # 사용자에게 입력을 강제하지 않습니다 — 카카오 로그인 한 번으로 시작하게 하는 것이
    # 앱의 목표라, 첫 화면이 "이미 사용 중입니다"로 거절하면 그 약속이 깨집니다.
    #
    # **NULL 은 "아직 발급 전"입니다.** 이 컬럼보다 먼저 가입한 회원과 탈퇴로 지워진
    # 회원이 그렇고, 둘 다 다음 로그인에서 채워집니다.
    #
    # **유일성은 `lower(nickname)` 표현식 UNIQUE 인덱스가 잡습니다** — 아래
    # `__table_args__` 가 아니라 `db/init/03_auth.sql` 이 원본입니다. 컬럼에 `unique=True`
    # 를 걸지 않은 것은 그것이 대소문자를 가리기 때문입니다: 'Neo' 와 'neo' 가 둘 다
    # 생기면 화면에서 같은 이름으로 읽혀서 "고유하게 구분한다"가 그 자리에서 깨집니다.
    nickname: Mapped[str | None] = mapped_column(String(30))

    # 대표 강아지. 상단바·챗봇 얼굴이 이 아이를 따릅니다.
    #
    # **pets 쪽에 is_primary 를 두지 않은 이유**는 05_pets.sql 에 적어 두었습니다 —
    # 요약하면 계정에 한 칸을 두어야 "한 마리만 대표"를 DB 가 저절로 보장합니다.
    # 그 강아지가 지워지면 NULL 이 되고, 승계는 서비스 계층이 합니다.
    primary_pet_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("pets.id", ondelete="SET NULL")
    )

    # 영수증 OCR 항목(vet_visits.raw_ocr_items)을 서비스 제공 범위 밖의 목적(진단 추천
    # 모델 학습)으로 쓰는 데 대한 동의 (#353, docs/vet-visits.md §3).
    #
    # **불리언이 아니라 시각입니다** — `ocr_consent_at IS NOT NULL` 이 곧 그 불리언이고,
    # 시각·판 번호까지 남아야 근거가 됩니다. **기본값이 없습니다** — DEFAULT 를 걸면
    # 아무도 누른 적 없는 동의가 전 회원에게 생깁니다. 철회는 둘 다 NULL 로 되돌립니다.
    ocr_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ocr_consent_version: Mapped[str | None] = mapped_column(String(20))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )

    def __repr__(self) -> str:
        # 암호문이라도 repr 로 흘리지 않습니다.
        return f"<AppUser kakao_id={self.kakao_id} status={self.status}>"
