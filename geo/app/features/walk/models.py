"""산책 기록 — 이 레포가 바깥에 **주는** 것. docs/contracts/walk-record.md

사실만 있다. 시간 · 거리 · 속도 · 정지 — 폰 GPS 가 ±2~5% 로 주는 것 (research/2026-08-19-walk-data-evidence.md).
목표 · 보상 · 점수 · 트리거 · 권유 · 서술은 **여기 없다.** 그건 이 모델을 소비하는 쪽의 일이고 전부 옵션이다.
사용자에게 보여줄 문장 필드도 없다 — 문자열은 식별자뿐이다.

필드 집합은 tests/test_walk_contract.py 가 고정한다. 하나 더하면 깨진다. 깨뜨리는 것이 보이는 결정이다.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RECORD_VERSION = 4
CALCULATION_VERSION = 4
ENCOUNTER_OCCURRENCE_VERSION = 3

EvidenceOrigin = Literal["device", "mock", "mixed", "unknown"]
SessionState = Literal["open", "sealed", "derived", "purged"]


class ContractModel(BaseModel):
    """바깥과 주고받는 형태. 모르는 값은 조용히 버리지 않는다."""

    model_config = ConfigDict(extra="forbid")


def _tz_required(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("walk timestamps must include a timezone")
    return value


class WalkFix(ContractModel):
    """수신 원본 한 점. 앱이 배치로 올린다. 속도는 여기 없다 — 점 둘 사이에서 계산한다."""

    # 네트워크 재전송과 업로드 순서가 측정열을 바꾸지 않게 클라이언트가 부여한다.
    client_seq: int = Field(ge=0)
    # 명시적 pause/resume 뒤 증가한다. 같은 세션이어도 서로 다른 chain은 잇지 않는다.
    chain_index: int = Field(0, ge=0)
    at: datetime
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    accuracy_m: float | None = Field(None, ge=0)
    # 재생·가짜 위치 표식. Android 가 DEVICE/MOCK 을 가르는 것의 서버 짝 —
    # 개발 재생 세션이 진짜 산책 사실처럼 쌓이지 않게 한다. 판정 아님, 표식만.
    is_mock: bool = False

    _tz = field_validator("at")(_tz_required)


class WalkSession(ContractModel):
    id: str = Field(min_length=1, max_length=128)
    dog_id: str = Field(min_length=1, max_length=128)
    started_at: datetime
    ended_at: datetime | None = None          # None = 진행 중
    fix_count: int = Field(0, ge=0)
    state: SessionState = "open"
    evidence_origin: EvidenceOrigin = "unknown"

    _tz = field_validator("started_at", "ended_at")(
        lambda v: v if v is None else _tz_required(v)
    )

    @model_validator(mode="after")
    def ends_after_start(self) -> "WalkSession":
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("ended_at must not precede started_at")
        return self


class WalkFacts(ContractModel):
    """세션이 끝난 뒤 코드가 계산한 사실. 바깥에 나가는 것은 이것이다."""

    # record v2 세션도 purge 뒤 계속 읽혀야 한다. 새 응답 기본값만 최신 버전이다.
    record_version: Literal[2, 3, RECORD_VERSION] = RECORD_VERSION
    calculation_version: int = Field(CALCULATION_VERSION, ge=1)
    session_id: str = Field(min_length=1, max_length=128)
    dog_id: str = Field(min_length=1, max_length=128)
    evidence_origin: EvidenceOrigin
    started_at: datetime
    ended_at: datetime

    duration_s: int = Field(ge=0)
    distance_m: int = Field(ge=0)             # 원 GPS 누적. 정지 중 지터 포함 — 참고값
    moving_distance_m: int = Field(ge=0)      # 속도 임계 이하 구간 0 처리. **거리는 이것이다**
    moving_s: int = Field(ge=0)
    stop_count: int = Field(ge=0)             # "정지"까지만. 냄새 맡기로 단정하지 않는다
    stop_s: int = Field(ge=0)
    avg_speed_mps: float | None = Field(None, ge=0)   # moving 기준. moving_s 가 0 이면 None
    fix_count: int = Field(ge=0)

    _tz = field_validator("started_at", "ended_at")(_tz_required)

    @model_validator(mode="after")
    def consistent(self) -> "WalkFacts":
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must not precede started_at")
        if self.moving_distance_m > self.distance_m:
            raise ValueError("moving_distance_m cannot exceed distance_m")
        if self.moving_s + self.stop_s > self.duration_s:
            raise ValueError("moving_s + stop_s cannot exceed duration_s")
        wall_s = round((self.ended_at - self.started_at).total_seconds())
        if self.duration_s > wall_s:
            raise ValueError("duration_s cannot exceed the session wall time")
        return self


class MotionEventOccurrence(ContractModel):
    """세션 안에서 관측된 상태 변화. 이유·장소 의미는 붙이지 않는다."""

    session_id: str = Field(min_length=1, max_length=128)
    event_index: int = Field(ge=0)
    type: Literal["stop"] = "stop"
    started_at: datetime
    ended_at: datetime
    duration_s: int = Field(ge=0)
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    route_offset_m: float = Field(ge=0)
    accuracy_p50_m: float | None = Field(None, ge=0)
    fix_count: int = Field(ge=2)

    _tz = field_validator("started_at", "ended_at")(_tz_required)

    @model_validator(mode="after")
    def ends_after_start(self) -> "MotionEventOccurrence":
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must not precede started_at")
        return self


class FacilityEncounter(ContractModel):
    """동선 주변에 시설 좌표가 있었다는 관측 — 기하값까지만.

    "지나쳤다/봤다/들렀다"는 여기 없다. 그 판정은 이 사실을 소비하는 쪽(app/features/scene)이
    규칙표+버전으로 한다. 시설 좌표는 대표점(건물 중심)이지 출입구가 아니다.

    밴드(10/15/20m)를 전부 저장하는 이유: 원좌표는 finish에서 지워지므로, 판정 반지름을
    실측(반복 보행) 후 정하려면 후보 반지름들의 답이 미리 계산돼 있어야 한다.

    폐업·인허가 상태는 거르는 조건이 아니라 싣는 데이터다(place_active) — 관측층은
    큐레이션하지 않는다. 폐업한 병원 앞을 지나는 것도 사실이다.
    """

    session_id: str = Field(min_length=1, max_length=128)
    event_index: int = Field(ge=0)
    occurrence_version: int = Field(ENCOUNTER_OCCURRENCE_VERSION, ge=1)
    occurrence_index: int | None = Field(None, ge=0)  # 같은 시설의 세션 내 n번째 연속 진입
    entered_at: datetime | None = None                # 관측된 원 내부 구간 시작
    exited_at: datetime | None = None                 # 관측된 원 내부 구간 끝
    entry_observed: bool | None = None                # 실제 원 경계를 통과하며 시작했나
    exit_observed: bool | None = None                 # 실제 원 경계를 통과하며 끝났나
    entered_offset_m: float | None = Field(None, ge=0)
    exited_offset_m: float | None = Field(None, ge=0)
    facility_source: str = Field(min_length=1, max_length=64)   # kcisa | kto | ...
    facility_ref: str = Field(min_length=1, max_length=128)     # 안정 키. facility.id 아님
    kind: str = Field(min_length=1, max_length=32)
    lat: float = Field(ge=-90, le=90)                           # 시설 대표점 (공개 장소)
    lng: float = Field(ge=-180, le=180)
    place_active: bool | None = None       # 의료 오버레이 상태. 비의료·미링크는 None
    as_of: date | None = None              # 시설 원천 기준일

    min_lateral_m: float = Field(ge=0)     # 동선이 시설에 가장 가까웠던 거리
    offset_m: float = Field(ge=0)          # 그 순간의 동선상 위치 (이동거리 기준)
    dwell_s_10m: int = Field(ge=0)         # 반지름별 체류 시간 — 판정 원 후보 3개
    dwell_s_15m: int = Field(ge=0)
    dwell_s_20m: int = Field(ge=0)
    pass_count: int = Field(ge=0)          # 20m 원 진입 횟수 (왕복이면 2)
    stop_overlap_10m: bool = False         # 그 원 안에서 정지 이벤트가 있었나
    stop_overlap_15m: bool = False
    stop_overlap_20m: bool = False
    stop_s_10m: int = Field(0, ge=0)       # 10m 원 안 정지 이벤트 지속시간 합
    accuracy_p50_m: float | None = Field(None, ge=0)   # 20m 원 안 관측점 정확도 중앙값

    _occurrence_tz = field_validator("entered_at", "exited_at")(
        lambda v: v if v is None else _tz_required(v)
    )

    @model_validator(mode="after")
    def occurrence_is_complete_in_current(self) -> "FacilityEncounter":
        if self.occurrence_version < ENCOUNTER_OCCURRENCE_VERSION:
            return self                              # v1 집계행은 원좌표 삭제로 backfill 불가
        required = {
            "occurrence_index": self.occurrence_index,
            "entered_at": self.entered_at,
            "exited_at": self.exited_at,
            "entry_observed": self.entry_observed,
            "exit_observed": self.exit_observed,
            "entered_offset_m": self.entered_offset_m,
            "exited_offset_m": self.exited_offset_m,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise ValueError(f"occurrence v3 requires {', '.join(missing)}")
        if self.exited_at < self.entered_at:
            raise ValueError("exited_at must not precede entered_at")
        if self.exited_offset_m < self.entered_offset_m:
            raise ValueError("exited_offset_m must not precede entered_offset_m")
        if self.pass_count != 1:
            raise ValueError("occurrence v3 represents exactly one pass")
        return self
