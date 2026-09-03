"""앱 회원의 산책 기록. 원본 스키마는 `db/init/06_walks.sql` 입니다.

**끝난 산책만 들어옵니다.** 기기가 강제 종료되어 열린 채 남은 세션은 기록이 아니라
사고의 흔적이라 앱이 올리지 않습니다 — 그래서 `ended_at` 이 NOT NULL 입니다.

거리와 시간은 **컬럼이 없습니다.** 좌표에서 다시 계산하는 값이라 저장하면 계산 규칙을
고쳤을 때 저장된 숫자와 새로 계산한 숫자가 갈라집니다. 앱의 `WalkSummary` 도 같은
이유로 요약을 저장하지 않습니다.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from daengs_backend.models.base import Base

WALK_ANALYSIS_STATES = ("collecting", "derived")


class Walk(Base):
    __tablename__ = "walks"

    __table_args__ = (
        CheckConstraint("ended_at >= started_at", name="walks_time_order"),
        CheckConstraint(
            "analysis_state IN ('collecting','derived')",
            name="walks_analysis_state_check",
        ),
        # **재시도가 안전해야 합니다.** 앱은 네트워크가 끊기면 다음에 다시 올리는데,
        # 그때 같은 산책이 두 건이 되면 안 됩니다.
        UniqueConstraint(
            "app_user_id", "client_session_id", name="walks_client_session_unique"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=text("gen_random_uuid()")
    )

    # 계정이 지워지면 산책도 같이 지웁니다. 산책 경로는 집과 생활권을 그대로
    # 드러내므로, 탈퇴가 개인정보를 파기하는데 이게 남으면 파기가 반쪽입니다.
    app_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("app_users.id", ondelete="CASCADE")
    )

    # 누구와 걸었나는 walk_pets 에 있습니다 (아래 WalkPet). 한 번에 두 마리를
    # 데리고 나가므로 한 칸으로는 못 담습니다.

    # 기기의 로컬 DB 와 **같은 값**입니다. 올릴 때도 되찾을 때도 이 id 로 맞춰 봅니다.
    client_session_id: Mapped[uuid.UUID] = mapped_column(Uuid)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # 나갈 때의 날씨. WMO 원본 코드입니다. 못 받았으면 셋 다 None 이고,
    # **그걸 "맑음"으로 채우지 않습니다.**
    weather_code: Mapped[int | None] = mapped_column(Integer)
    is_day: Mapped[bool | None] = mapped_column(Boolean)
    temperature_c: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))

    # 좌표 입력을 더 받을 수 있는지 나타냅니다. 계산 세대는 아래 WalkAnalysis가 따로
    # 가지므로, 새 정책으로 재분석한다고 이 값을 되돌리지 않습니다.
    # 기존 DB migration은 배포 후 수동 적용하므로 평소 select(Walk)에서는 뺀다.
    # finalize 서비스는 migration 적용 후 undefer해 행 잠금과 같이 읽어야 한다.
    analysis_state: Mapped[str] = mapped_column(
        String(16),
        server_default=text("'collecting'"),
        deferred=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()")
    )

    points: Mapped[list["WalkPointChunk"]] = relationship(
        back_populates="walk",
        cascade="all, delete-orphan",
        order_by="WalkPointChunk.seq_from",
    )

    analyses: Mapped[list["WalkAnalysis"]] = relationship(
        back_populates="walk",
        cascade="all, delete-orphan",
        order_by="WalkAnalysis.derived_at",
    )

    # 그 산책에 나간 아이들. 순서는 pet_id 로 고정합니다 — 목록이 새로고침할 때마다
    # 뒤바뀌면 앱이 "바뀌었다" 로 읽습니다.
    pets: Mapped[list["WalkPet"]] = relationship(
        back_populates="walk",
        cascade="all, delete-orphan",
        order_by="WalkPet.pet_id",
    )

    @property
    def pet_ids(self) -> list[uuid.UUID]:
        """응답에 쓰는 모양. **관계가 이미 로드돼 있어야 합니다** —

        비동기 세션에서 지연 로딩은 접근하는 순간 터집니다. 조회하는 쪽이
        `selectinload(Walk.pets)` 를 붙입니다.
        """
        return [link.pet_id for link in self.pets]

    def __repr__(self) -> str:
        return f"<Walk {self.id} started={self.started_at}>"


class WalkPointChunk(Base):
    """기기가 준 **원본 좌표**를 묶음으로. 화면용으로 거르기 전의 값입니다.

    흔들림을 걸러내는 문턱값은 나중에 바뀔 수 있고, 그때 버린 점을 되살릴 수 있어야
    합니다 — 기기의 로컬 DB 가 원본만 남기는 것과 같은 이유입니다. **하나도 안 버립니다.**

    **왜 점마다 한 줄이 아닌가.** 예전에는 좌표 한 점에 한 줄(`walk_points`)이었습니다.
    실기기 실측으로 초당 1.02점이 쌓여 30분 산책이면 1,842줄인데, **이 좌표를 조건으로
    거는 질의가 하나도 없습니다** — 늘 한 산책의 전부를 통째로 읽어 JSON 으로 내보낼
    뿐입니다. 점당 실제 데이터는 12바이트쯤인데 행 하나에 124바이트를 냈습니다.

        실측(131점 트랙) : 점당 한 줄 124 B → jsonb 배열 22 B  (5.6배)

    `payload` 의 모양과 그렇게 정한 이유는 `services/walk_chunk.py` 에 있습니다.

    **보관 기간은 탈퇴 시까지입니다.** 아래 CASCADE 가 그것을 보장하고, 따로 만료
    배치를 두지 않습니다 (2026-09-02 팀 결정).
    """

    __tablename__ = "walk_point_chunks"

    walk_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("walks.id", ondelete="CASCADE"), primary_key=True
    )

    # PK 의 일부입니다 — **같은 묶음을 두 번 보내도 한 줄**입니다. 예전 `client_seq`
    # 가 하던 일을 묶음 단위로 옮긴 것입니다.
    seq_from: Mapped[int] = mapped_column(Integer, primary_key=True)

    # 재시도 판정을 payload 를 풀지 않고 하려고 둡니다.
    seq_to: Mapped[int] = mapped_column(Integer)

    # 세려고 payload 를 풀지 않게 합니다.
    point_count: Mapped[int] = mapped_column(Integer)

    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)

    walk: Mapped[Walk] = relationship(back_populates="points")


class WalkAnalysis(Base):
    """봉인된 한 입력을 한 Walk 계산 세대로 해석한 불변 결과."""

    __tablename__ = "walk_analyses"

    __table_args__ = (
        CheckConstraint(
            "input_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
            name="walk_analyses_input_fingerprint_check",
        ),
        CheckConstraint("point_count >= 0", name="walk_analyses_point_count_check"),
        CheckConstraint(
            "(point_count = 0 AND terminal_client_seq IS NULL) OR "
            "(point_count > 0 AND terminal_client_seq = point_count - 1)",
            name="walk_analyses_terminal_sequence_check",
        ),
        CheckConstraint(
            "facts_record_version > 0 AND calculation_version > 0 "
            "AND receipt_version > 0 AND observation_version > 0",
            name="walk_analyses_versions_positive",
        ),
        CheckConstraint(
            "moving_distance_m >= 0 AND moving_s >= 0 AND stop_count >= 0",
            name="walk_analyses_summary_nonnegative",
        ),
        CheckConstraint("jsonb_typeof(facts) = 'object'", name="walk_analyses_facts_object"),
        CheckConstraint(
            "jsonb_typeof(measurement_receipt) = 'object'",
            name="walk_analyses_receipt_object",
        ),
        CheckConstraint(
            "jsonb_typeof(motion_events) = 'array'",
            name="walk_analyses_events_array",
        ),
        CheckConstraint(
            "jsonb_typeof(micro_observations) = 'array'",
            name="walk_analyses_observations_array",
        ),
        UniqueConstraint(
            "walk_id",
            "input_fingerprint",
            "facts_record_version",
            "calculation_version",
            "receipt_version",
            "observation_version",
            name="walk_analyses_identity_unique",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    walk_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("walks.id", ondelete="CASCADE"),
    )
    input_fingerprint: Mapped[str] = mapped_column(String(71))
    point_count: Mapped[int] = mapped_column(Integer)
    terminal_client_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)

    facts_record_version: Mapped[int] = mapped_column(Integer)
    calculation_version: Mapped[int] = mapped_column(Integer)
    receipt_version: Mapped[int] = mapped_column(Integer)
    observation_version: Mapped[int] = mapped_column(Integer)

    # 목록·집계가 먼저 요구할 세 값만 밖으로 꺼냅니다. 전체 계약은 JSONB가 보존합니다.
    moving_distance_m: Mapped[int] = mapped_column(Integer)
    moving_s: Mapped[int] = mapped_column(Integer)
    stop_count: Mapped[int] = mapped_column(Integer)

    facts: Mapped[dict[str, Any]] = mapped_column(JSONB)
    measurement_receipt: Mapped[dict[str, Any]] = mapped_column(JSONB)
    motion_events: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    micro_observations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)

    derived_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("NOW()"),
    )

    walk: Mapped[Walk] = relationship(back_populates="analyses")
    cellophane_sheets: Mapped[list["WalkCellophaneSheet"]] = relationship(
        back_populates="analysis",
        cascade="all, delete-orphan",
        order_by="WalkCellophaneSheet.paint_fp",
    )
    capsule: Mapped["WalkCapsule | None"] = relationship(
        back_populates="analysis",
        cascade="all, delete-orphan",
        uselist=False,
    )


class WalkCellophaneSheet(Base):
    """한 분석 결과를 특정 Paint spec으로 칠한 compact canonical sheet."""

    __tablename__ = "walk_cellophane_sheets"

    __table_args__ = (
        CheckConstraint(
            "sheet_schema_version > 0 AND paint_version > 0",
            name="walk_cellophane_versions_positive",
        ),
        CheckConstraint(
            "radius_u > 0 AND sample_step_m > 0",
            name="walk_cellophane_spec_positive",
        ),
        CheckConstraint(
            "paint_fp <> '' AND grid_version <> '' AND profile <> '' AND profile_fp <> ''",
            name="walk_cellophane_identity_nonempty",
        ),
        CheckConstraint("cell_count >= 0", name="walk_cellophane_cell_count_check"),
        CheckConstraint(
            "sheet_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
            name="walk_cellophane_fingerprint_check",
        ),
        CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="walk_cellophane_payload_object",
        ),
    )

    analysis_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("walk_analyses.id", ondelete="CASCADE"),
        primary_key=True,
    )
    paint_fp: Mapped[str] = mapped_column(String(128), primary_key=True)
    sheet_schema_version: Mapped[int] = mapped_column(Integer)
    paint_version: Mapped[int] = mapped_column(Integer)
    grid_version: Mapped[str] = mapped_column(String(64))
    radius_u: Mapped[float] = mapped_column(Float)
    profile: Mapped[str] = mapped_column(String(128))
    profile_fp: Mapped[str] = mapped_column(String(128))
    sample_step_m: Mapped[float] = mapped_column(Float)
    cell_count: Mapped[int] = mapped_column(Integer)
    sheet_fingerprint: Mapped[str] = mapped_column(String(71))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    derived_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("NOW()"),
    )

    analysis: Mapped[WalkAnalysis] = relationship(back_populates="cellophane_sheets")


class WalkCapsule(Base):
    """한 WalkAnalysis의 필수 원판이 모두 준비됐음을 선언하는 마지막 seal."""

    __tablename__ = "walk_capsules"

    __table_args__ = (
        CheckConstraint(
            "capsule_version > 0 AND context_version > 0",
            name="walk_capsules_versions_positive",
        ),
        CheckConstraint(
            "jsonb_typeof(capabilities) = 'array' AND jsonb_array_length(capabilities) > 0",
            name="walk_capsules_capabilities_array",
        ),
        CheckConstraint(
            "jsonb_typeof(trail_context) = 'object'",
            name="walk_capsules_context_object",
        ),
    )

    analysis_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("walk_analyses.id", ondelete="CASCADE"),
        primary_key=True,
    )
    capsule_version: Mapped[int] = mapped_column(Integer)
    context_version: Mapped[int] = mapped_column(Integer)
    capabilities: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    trail_context: Mapped[dict[str, Any]] = mapped_column(JSONB)
    sealed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    analysis: Mapped[WalkAnalysis] = relationship(back_populates="capsule")


class WalkPet(Base):
    """그 산책에 누가 나갔나. 원본 스키마는 `db/init/06_walks.sql` 입니다.

    **한 번에 여러 마리를 데리고 나갑니다.** `walks.pet_id` 한 칸이던 것을 조인으로
    옮긴 이유입니다 — 두 마리를 데리고 나갔는데 한 아이의 기록만 남으면, 나중에
    챗봇이 "이 아이 이번 주 운동량" 을 말할 때 나머지 아이의 산책이 통째로 빕니다.
    """

    __tablename__ = "walk_pets"

    walk_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("walks.id", ondelete="CASCADE"), primary_key=True
    )

    # **여기서는 CASCADE 입니다.** 단수 pet_id 일 때는 SET NULL 이었습니다 —
    # 무지개다리를 건넌 아이와의 산책이 없던 일이 되면 안 되니까요. 뜻은 그대로입니다:
    # 강아지를 지우면 이 연결만 사라지고 **산책 자체는 남습니다.** 조인 행에 NULL 을
    # 남기면 "누군지 모를 아이" 라는 뜻 없는 줄이 쌓입니다.
    pet_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pets.id", ondelete="CASCADE"), primary_key=True
    )

    walk: Mapped[Walk] = relationship(back_populates="pets")

    def __repr__(self) -> str:
        return f"<WalkPet walk={self.walk_id} pet={self.pet_id}>"
