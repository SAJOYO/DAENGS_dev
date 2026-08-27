"""산책 패키지는 수집만 한다 — 이 약속을 테스트로 고정한다.

문구는 또 읽힌다. 타입은 안 읽히면 깨진다. 필드 하나를 더하면 여기가 깨지고,
깨뜨리는 것이 **보이는 결정**이 된다. docs/contracts/walk-record.md
"""

import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from app.features import walk
from app.features.walk import models
from app.features.walk.models import (
    FacilityEncounter,
    MotionEventOccurrence,
    WalkFacts,
    WalkFix,
    WalkSession,
)

T0 = datetime(2026, 8, 22, 7, 0, tzinfo=UTC)


def _models() -> list[type[BaseModel]]:
    return [m for _, m in inspect.getmembers(models, inspect.isclass)
            if issubclass(m, BaseModel) and m.__module__ == models.__name__
            and m is not models.ContractModel]


# ------------------------------------------------------------------ 집합 고정
def test_field_sets_are_pinned():
    """계약의 필드 집합 그대로. 늘리려면 walk-record.md 와 record_version 을 같이 올려라."""
    assert set(WalkFix.model_fields) == {
        "client_seq", "chain_index", "at", "lat", "lng", "accuracy_m", "is_mock",
    }
    assert set(WalkSession.model_fields) == {
        "id", "dog_id", "started_at", "ended_at", "fix_count", "state",
        "evidence_origin",
    }
    assert set(WalkFacts.model_fields) == {
        "record_version", "calculation_version", "session_id", "dog_id",
        "evidence_origin", "started_at", "ended_at",
        "duration_s", "distance_m", "moving_distance_m", "moving_s",
        "stop_count", "stop_s", "avg_speed_mps", "fix_count",
    }
    assert set(MotionEventOccurrence.model_fields) == {
        "session_id", "event_index", "type", "started_at", "ended_at", "duration_s",
        "lat", "lng", "route_offset_m", "accuracy_p50_m", "fix_count",
    }
    assert set(FacilityEncounter.model_fields) == {
        "session_id", "event_index", "occurrence_version", "occurrence_index",
        "entered_at", "exited_at", "entry_observed", "exit_observed",
        "entered_offset_m", "exited_offset_m",
        "facility_source", "facility_ref", "kind",
        "lat", "lng", "place_active", "as_of", "min_lateral_m", "offset_m",
        "dwell_s_10m", "dwell_s_15m", "dwell_s_20m", "pass_count",
        "stop_overlap_10m", "stop_overlap_15m", "stop_overlap_20m", "stop_s_10m",
        "accuracy_p50_m",
    }
    assert {m.__name__ for m in _models()} == {
        "WalkFix", "WalkSession", "WalkFacts", "MotionEventOccurrence",
        "FacilityEncounter",
    }


# ------------------------------------------------------------------ 의미 없음
def test_no_field_carries_meaning():
    """목표·보상·트리거·서술… 은 사실이 아니다. 이름에 그 토큰이 보이면 경계를 넘은 것이다."""
    for m in _models():
        for name in m.model_fields:
            hit = [t for t in walk.OUT_OF_SCOPE_TOKENS if t in name.lower()]
            assert not hit, f"{m.__name__}.{name} 은 수집이 아니라 의미다: {hit}"


def test_no_text_for_display():
    """문자열 필드는 식별자뿐이다. 사용자에게 보여줄 문장은 소비자가 만든다."""
    # facility_source/ref/kind 는 문장이 아니라 시설의 안정 식별자다 (facility 층의 키)
    allowed = {"id", "dog_id", "session_id", "facility_source", "facility_ref", "kind"}
    for m in _models():
        for name, f in m.model_fields.items():
            if f.annotation is str:
                assert name in allowed, f"{m.__name__}.{name} — 문장 필드는 여기 없다"


def test_walk_package_has_no_judgment_modules():
    """advice.py · reward.py · narration.py 같은 파일이 이 패키지에 생기면 경계가 무너진 것이다."""
    pkg = Path(walk.__file__).parent
    names = {p.stem for p in pkg.glob("*.py")} - {"__init__", "models"}
    bad = [n for n in names if any(t in n for t in walk.OUT_OF_SCOPE_TOKENS)]
    assert not bad, f"수집 패키지에 판정 모듈: {bad}"
    # 엔드포인트·사실 계산이 생기면 여기 허용 목록에 이름을 **명시적으로** 더한다.
    # curve: 진행 구간별 이동·정지 시간. 숫자만 내고 "지쳤다" 같은 해석은 소비자 몫이다.
    assert names <= {"api", "facts", "store", "encounter", "curve"}, f"예상 밖 모듈: {names}"


# ------------------------------------------------------------------ 계약 검증
def test_timestamps_require_timezone():
    naive = datetime(2026, 8, 22, 7, 0)  # noqa: DTZ001 — 일부러 naive. 거부돼야 한다
    with pytest.raises(ValidationError, match="timezone"):
        WalkFix(client_seq=0, at=naive, lat=37.5, lng=127.0)
    with pytest.raises(ValidationError, match="timezone"):
        WalkSession(id="s1", dog_id="halmae", started_at=naive)


def test_session_cannot_end_before_it_starts():
    with pytest.raises(ValidationError, match="precede"):
        WalkSession(id="s1", dog_id="halmae", started_at=T0, ended_at=T0 - timedelta(minutes=1))


def test_facts_are_internally_consistent():
    ok = {"session_id": "s1", "dog_id": "halmae", "evidence_origin": "device",
          "started_at": T0, "ended_at": T0 + timedelta(minutes=20),
          "duration_s": 1200, "distance_m": 1500, "moving_distance_m": 1380, "moving_s": 1000,
          "stop_count": 3, "stop_s": 200, "avg_speed_mps": 1.38, "fix_count": 240}
    f = WalkFacts(**ok)
    assert f.record_version == models.RECORD_VERSION

    with pytest.raises(ValidationError, match="exceed distance_m"):
        WalkFacts(**{**ok, "moving_distance_m": 1600})
    with pytest.raises(ValidationError, match="exceed duration_s"):
        WalkFacts(**{**ok, "stop_s": 300})


def test_unknown_fields_are_rejected_not_dropped():
    """소비자가 `goal_min` 을 실어 보내도 조용히 버리지 않는다 — 계약 위반은 보여야 한다."""
    with pytest.raises(ValidationError, match="extra"):
        WalkFix(client_seq=0, at=T0, lat=37.5, lng=127.0, goal_min=30)


def test_legacy_aggregate_encounter_is_readable_but_new_rows_require_occurrence_bounds():
    old_fields = {
        "session_id": "old", "event_index": 0,
        "facility_source": "kcisa", "facility_ref": "legacy", "kind": "cafe",
        "lat": 37.5, "lng": 127.0, "min_lateral_m": 5, "offset_m": 100,
        "dwell_s_10m": 5, "dwell_s_15m": 20, "dwell_s_20m": 40,
        "pass_count": 2,
    }
    legacy = FacilityEncounter(occurrence_version=1, **old_fields)
    assert legacy.occurrence_index is None       # purge된 v1 합계행은 분할한 척하지 않는다

    with pytest.raises(ValidationError, match="occurrence v3 requires"):
        FacilityEncounter(**old_fields)
