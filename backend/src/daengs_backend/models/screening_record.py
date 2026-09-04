"""피부 변화 기록 — backend 가 소유하는 새 계약 `/app/screening/*` (D-052).

스키마 원본은 `db/init/09_screening_records.sql` 입니다. 이 모델은 그 SQL 을 따라가는
쪽이라, SQL 을 고치면 여기도 손으로 맞춰야 합니다 (저장소 규칙).

⚠️ **옛 경로 `/screen/v1/screen` 은 이 표를 안 씁니다.** 그쪽은 인증 없이 판정만 하고
   아무것도 안 남깁니다. 앱이 새 계약으로 옮겨간 뒤 옛 경로를 410 으로 닫는 것은
   별도 카드입니다 — 보행이 `/gait/*` → `/app/gait/*` 로 옮길 때와 같은 방식입니다.

⚠️ **사진을 판정 뒤에도 안 지웁니다.** 점령지 사진은 판정이 끝나면 0바이트로 덮지만
   (`redact`), 여기는 **사진 자체가 기록**입니다 — 지난 사진과 나란히 놓고 보는 것이
   이 기능입니다. 보관은 "탈퇴 시 지체 없이 파기" 하나뿐입니다 (D-052 B).
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
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from daengs_backend.models.base import Base

#: 파이프라인 상태. 전이는 `services/screening.py` 만 합니다.
#:
#: 보행(`GAIT_STATUSES`)보다 짧습니다 — 판정이 **동기**라 PROCESSING 이 없습니다.
#: 스크리닝 모델은 사진 한 장에 CPU 0.6~3초라 워커로 뺄 만큼 길지 않습니다.
SCREENING_STATUSES = ("PENDING_UPLOAD", "DONE", "FAILED")


class ScreeningRecord(Base):
    __tablename__ = "screening_records"

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING_UPLOAD','DONE','FAILED')",
            name="screening_records_status_check",
        ),
        CheckConstraint(
            "photo_content_type IN ('image/jpeg','image/webp')",
            name="screening_records_photo_content_type_check",
        ),
        CheckConstraint(
            "photo_size_bytes IS NULL OR photo_size_bytes > 0",
            name="screening_records_photo_size_check",
        ),
        Index("idx_screening_records_pet_created", "pet_id", "created_at"),
        Index("idx_screening_records_owner_created", "app_user_id", "created_at"),
        Index("idx_screening_records_photo_key", "photo_storage_key", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )

    #: 누구의 기록인가.
    #:
    #: ⚠️ **이 CASCADE 에 기대면 안 됩니다.** 탈퇴는 `app_users` 행을 **남기므로**
    #:    (kakao_id 로 "이미 탈퇴한 사람" 을 알아보려고) 영영 안 돕니다.
    #:    탈퇴 경로가 명시로 지웁니다 — 대화(chats)가 같은 이유로 명시 삭제입니다.
    app_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app_users.id", ondelete="CASCADE")
    )

    #: 어느 아이의 피부인가. **None 을 허용합니다** — 아이를 지워도 기록은 남습니다
    #: (FK 가 SET NULL). 사진은 탈퇴 때 지웁니다.
    pet_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("pets.id", ondelete="SET NULL")
    )

    status: Mapped[str] = mapped_column(String(20), server_default=text("'PENDING_UPLOAD'"))

    #: 사진이 있는 곳. 키는 **backend 가 만듭니다**(원칙 6).
    photo_storage_key: Mapped[str] = mapped_column(String(200))
    photo_content_type: Mapped[str] = mapped_column(String(40))

    #: 저장소 세대값. 로컬 볼륨은 sha256 hex, GCS 는 숫자 문자열이라 **문자**입니다.
    photo_generation: Mapped[str | None] = mapped_column(String(64))
    photo_size_bytes: Mapped[int | None] = mapped_column(Integer)

    #: 앱의 가이드 프레임 (정규화 `[x, y, w, h]`). 학습과 같은 함수로 자르는 데 씁니다.
    box: Mapped[list | None] = mapped_column(JSONB)

    #: 판정 결과 원본. **열로 펼치지 않습니다** — 모델 계약이 바뀌어도 옛 기록은
    #: 그대로 남아야 하고, 무엇으로 판정했는지는 `contract_version` 이 들고 있습니다.
    result: Mapped[dict | None] = mapped_column(JSONB)
    contract_version: Mapped[str | None] = mapped_column(String(20))

    #: 실패 사유. **사용자에게 그대로 보여 주지 않습니다** (운영자용).
    failure_reason: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )

    def __repr__(self) -> str:
        return f"<ScreeningRecord {self.id} status={self.status}>"
