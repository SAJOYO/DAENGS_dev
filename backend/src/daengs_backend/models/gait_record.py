"""보행 분석 기록 — backend 가 소유하는 record/job 원장 (D-043).

스키마 원본은 `db/init/07_gait_records.sql` 입니다. 이 모델은 그 SQL 을 따라가는
쪽이라, SQL 을 고치면 여기도 손으로 맞춰야 합니다 (저장소 규칙).

⚠️ `status` 와 `quality_status` 는 **다른 축**입니다 — 전자는 파이프라인이 어디까지
   갔나(FAILED = 재시도), 후자는 분석해 보니 쓸 만한가(unavailable = 재촬영).
   섞으면 사용자 안내가 갈리지 않습니다.

⚠️ `internal_feature_vector` 는 **API 응답이 절대 내보내면 안 됩니다** — 수백 개의
   숫자가 화면에 나오면 사용자가 건강 점수로 읽습니다. 스키마(`schemas/gait.py`)가
   이 컬럼을 아예 모르는 것으로 강제합니다.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from daengs_backend.models.base import Base

# 파이프라인 상태. 전이는 services/gait.py 만 합니다.
GAIT_STATUSES = ("PENDING", "UPLOADED", "PROCESSING", "DONE", "FAILED")


class GaitRecord(Base):
    __tablename__ = "gait_records"

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','UPLOADED','PROCESSING','DONE','FAILED')",
            name="gait_records_status_check",
        ),
        CheckConstraint(
            "quality_status IN ('ok','unavailable')",
            name="gait_records_quality_status_check",
        ),
        # ⚠ db/init/07_gait_records.sql 과 같아야 한다. 엔진(legacy·v4)이 내는 세 단계
        #   good/ok/low 전부 — tests/test_gait_quality_tier_contract.py 가 SQL·엔진과 대조한다.
        CheckConstraint(
            "quality_tier IN ('good','ok','low')",
            name="gait_records_quality_tier_check",
        ),
        Index("idx_gait_records_pet_created", "pet_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )

    # 소유권은 pet_id → pets.app_user_id 로 **유도**합니다. owner_user_id 를 중복
    # 저장하면 반려견 양도 같은 경우에 어긋납니다.
    pet_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pets.id", ondelete="CASCADE")
    )

    # ⚠️ **이것은 소유권이 아닙니다 — 업로드한 사람입니다.** 위 주석대로 소유는 여전히
    # `pet_id → pets.app_user_id` 로만 유도됩니다. 이 칸이 생긴 이유는 Task 19 —
    # `start_analysis` 가 구성원(대표 ∪ 돌보미)에게 열려 있는데 `confirm_upload` 는
    # 대표만이라, 돌보미가 티켓 발급·업로드까지는 성공하고 **confirm 에서만 404** 를
    # 받는 "반쯤 열린" 상태였습니다(docs/co-care.md §2 "보행 carer's gait recording is
    # broken halfway"). 고치는 방법은 확정 권한을 구성원 전체로 여는 것이 아니라
    # **업로더 본인 또는 대표**로 좁히는 것입니다(`care_repo.get_deletable` 과 같은 모양) —
    # 그러려면 "누가 올렸는지"를 알아야 하므로 이 칸이 필요합니다. 다음 사람이 이것을
    # "그럼 owner_user_id 를 하나 더 추가한 셈 아닌가" 로 **단순화하지 마세요** — 대표가
    # 바뀌어도(승계) 이 칸은 그대로 "그때 올린 사람"을 가리켜야 하고, 지운 권한 판단은
    # 언제나 `pet_id → pets.app_user_id`(대표) OR 이 칸(업로더) 둘의 OR 이지 이 칸
    # 하나가 아닙니다. NULL 은 이 칸이 생기기 전에 만들어진 옛 기록입니다 — 그런 기록은
    # confirm 이 이미 끝났거나 고아이므로 업로더를 몰라도 문제가 안 됩니다.
    actor_app_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("app_users.id", ondelete="SET NULL")
    )

    status: Mapped[str] = mapped_column(
        String(20), server_default=text("'PENDING'")
    )
    quality_status: Mapped[str | None] = mapped_column(String(20))
    quality_tier: Mapped[str | None] = mapped_column(String(10))

    # 클라우드 저장소(#78)의 불투명 키. provider 미정이라 형식을 강제하지 않습니다.
    original_storage_key: Mapped[str | None] = mapped_column(Text)
    overlay_storage_key: Mapped[str | None] = mapped_column(Text)

    quality: Mapped[dict | None] = mapped_column(JSONB)
    summary_for_ui: Mapped[dict | None] = mapped_column(JSONB)
    internal_feature_vector: Mapped[dict | None] = mapped_column(JSONB)

    gait_filter_version: Mapped[str | None] = mapped_column(Text)
    # 어떤 pose model / 관절 정의로 만든 기록인가 (D-063). 실행 엔진 선택이 아니라 메타데이터.
    # NULL = 판별 불가한 옛 기록, 또는 엔진 결과 없이 실패한 기록. 값의 정본은
    # daengs_gait.contract.POSE_MODELS.
    pose_model: Mapped[str | None] = mapped_column(Text)

    captured_at: Mapped[datetime.date | None] = mapped_column(Date)
    video_meta: Mapped[dict | None] = mapped_column(JSONB)
    source_file: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    failure_reason: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )
    # soft delete — 스토리지 파일 삭제가 비동기라 행을 먼저 지우면 키를 잃습니다.
    deleted_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
