"""encounter 기하와 판정 규칙표 — 히트박스 관측(사실)과 상태 분류(판정)의 경계 고정.

동쪽 직선 산책 fixture: 5초 간격 7m = 1.4m/s. 시설은 횡거리(북쪽)로 벌려 놓는다.
"""

from datetime import timedelta

from app.features.scene.judgment import JUDGMENT_VERSION, judge
from app.features.walk.encounter import FacilityCandidate, compute_encounters
from app.features.walk.facts import compute_facts
from app.features.walk.models import WalkFix
from tests.conftest import TEST_ORIGIN, WALK_T0, walk_fix


def cand(name_ref: str, east_m: float, north_m: float, **kw) -> FacilityCandidate:
    return FacilityCandidate(
        facility_source=kw.get("source", "kcisa"), facility_ref=name_ref,
        kind=kw.get("kind", "cafe"),
        lat=TEST_ORIGIN[0] + north_m / 111_320,
        lng=TEST_ORIGIN[1] + east_m / (111_320 * 0.7934),  # cos(37.4979°)
        place_active=kw.get("place_active"), as_of=None,
    )


def straight_walk(seconds: int = 300) -> list[WalkFix]:
    return [walk_fix(t, t / 5 * 7) for t in range(0, seconds + 5, 5)]


def compute(fixes, cands, ended_s=300):
    c = compute_facts("s1", "halmae", WALK_T0, WALK_T0 + timedelta(seconds=ended_s), fixes)
    return compute_encounters("s1", c.segments, c.events, cands), c


def test_geometry_offset_lateral_and_bands():
    """420m 직선 산책, 210m 지점 횡거리 12m 시설 — 기하값이 재현돼야 한다."""
    enc, _ = compute(straight_walk(300), [cand("f1", east_m=210, north_m=12)])
    assert len(enc) == 1
    e = enc[0]
    assert 11 <= e.min_lateral_m <= 13
    assert 200 <= e.offset_m <= 220
    assert e.dwell_s_10m == 0                       # 12m 횡거리 — 10m 원엔 못 들어온다
    assert e.dwell_s_15m > 0
    assert e.dwell_s_20m > e.dwell_s_15m            # 큰 원일수록 오래 머문다
    assert e.pass_count == 1


def test_far_facility_is_not_an_encounter():
    enc, _ = compute(straight_walk(300), [cand("far", east_m=210, north_m=80)])
    assert enc == []


def test_same_input_same_encounters():
    cands = [cand("f1", 210, 12), cand("f2", 100, 40)]
    a, _ = compute(straight_walk(300), cands)
    b, _ = compute(straight_walk(300), cands)
    assert [e.model_dump() for e in a] == [e.model_dump() for e in b]


def test_encounters_are_ordered_by_route_offset():
    enc, _ = compute(straight_walk(300),
                     [cand("late", 350, 8), cand("early", 70, 8)])
    assert [e.facility_ref for e in enc] == ["early", "late"]
    assert [e.event_index for e in enc] == [0, 1]


def test_out_and_back_emits_two_ordered_occurrences_for_same_facility():
    """같은 길의 반대 방향 통과를 시설별 세션 합계 하나로 접지 않는다."""
    east = list(range(0, 141, 7)) + list(range(133, -1, -7))
    fixes = [walk_fix(i * 5, distance) for i, distance in enumerate(east)]
    enc, _ = compute(
        fixes, [cand("same-facility", east_m=70, north_m=5)],
        ended_s=(len(fixes) - 1) * 5,
    )

    assert len(enc) == 2
    assert [e.facility_ref for e in enc] == ["same-facility", "same-facility"]
    assert [e.occurrence_index for e in enc] == [0, 1]
    assert [e.event_index for e in enc] == [0, 1]
    assert enc[0].entered_at < enc[0].exited_at < enc[1].entered_at < enc[1].exited_at
    assert enc[0].exited_offset_m < enc[1].entered_offset_m
    assert all(e.entry_observed and e.exit_observed and e.pass_count == 1 for e in enc)


def test_gap_inside_same_facility_splits_occurrences_with_unknown_boundaries():
    """수집 공백 동안 계속 원 안이었다고 가정하지 않는다."""
    fixes = [walk_fix(0, 60), walk_fix(5, 70), walk_fix(100, 70), walk_fix(105, 80)]
    enc, computed = compute(
        fixes, [cand("gap-facility", east_m=70, north_m=0)], ended_s=105,
    )

    assert computed.quality.gap_breaks == 1
    assert len(enc) == 2
    assert [e.occurrence_index for e in enc] == [0, 1]
    assert all(not e.entry_observed and not e.exit_observed for e in enc)


def test_closed_hospital_is_observed_not_filtered():
    """폐업은 필터가 아니라 데이터다 — 관측층은 큐레이션하지 않는다."""
    enc, _ = compute(straight_walk(300),
                     [cand("closed-hosp", 210, 12, kind="hospital", place_active=False)])
    assert len(enc) == 1
    assert enc[0].place_active is False


def test_stop_at_facility_marks_overlap_and_stop_seconds():
    """시설 바로 옆에서 35초 정지 — stop_overlap 과 stop_s_10m 에 잡힌다."""
    fixes = [walk_fix(t, t / 5 * 7) for t in range(0, 105, 5)]            # 0~100s → 140m
    fixes += [walk_fix(t, 140) for t in range(105, 140, 5)]               # 35초 정지 @140m
    fixes += [walk_fix(t, 140 + (t - 140) / 5 * 7) for t in range(140, 205, 5)]
    enc, c = compute(fixes, [cand("stopped-at", east_m=140, north_m=5)], ended_s=200)
    assert c.facts.stop_count == 1
    e = enc[0]
    assert e.stop_overlap_10m and e.stop_overlap_15m and e.stop_overlap_20m
    assert e.stop_s_10m >= 25


def test_stop_is_attached_only_to_the_overlapping_occurrence():
    """같은 시설을 두 번 지나도 첫 통과의 정지를 두 번째 통과에 복제하지 않는다."""
    east = list(range(0, 71, 7))
    east += [70] * 7                                  # 첫 통과에서만 35초 정지
    east += list(range(77, 141, 7))
    east += list(range(133, -1, -7))                  # 돌아올 때는 그대로 통과
    fixes = [walk_fix(i * 5, distance) for i, distance in enumerate(east)]
    enc, computed = compute(
        fixes, [cand("stopped-once", east_m=70, north_m=5)],
        ended_s=(len(fixes) - 1) * 5,
    )

    assert computed.facts.stop_count == 1
    assert len(enc) == 2
    assert enc[0].stop_overlap_10m and enc[0].stop_s_10m >= 30
    assert not enc[1].stop_overlap_10m and enc[1].stop_s_10m == 0


# ------------------------------------------------------------------ 판정층 (app/features/scene)
def test_judgment_passed_vs_lingered_vs_visited():
    fixes = [walk_fix(t, t / 5 * 7) for t in range(0, 105, 5)]
    fixes += [walk_fix(t, 140) for t in range(105, 140, 5)]
    fixes += [walk_fix(t, 140 + (t - 140) / 5 * 7) for t in range(140, 205, 5)]
    # drive-by 횡거리 16m: 20m 원엔 들어오지만 15m(기본 판정 반지름) 원엔 안 들어온다.
    # 참고 — 보행 1.4m/s 기준 횡거리 13.3m 이하면 15m 원 체류가 10초를 넘어 '머묾'이
    # 된다. 이 민감도가 LINGER_MIN_DWELL_S 가 잠정이고 실측이 필요한 이유다.
    enc, _ = compute(fixes, [
        cand("visited", east_m=140, north_m=5),      # 옆에서 35초 정지
        cand("drive-by", east_m=40, north_m=16),     # 20m 원만 스침
    ], ended_s=200)
    by_ref = {e.facility_ref: e for e in enc}
    assert judge(by_ref["visited"]) == "visited_guess"
    assert judge(by_ref["drive-by"]) == "passed"


def test_judgment_unjudgeable_when_accuracy_exceeds_band():
    fixes = [WalkFix(client_seq=i, at=WALK_T0 + timedelta(seconds=t), lat=TEST_ORIGIN[0],
                     lng=walk_fix(t, t / 5 * 7).lng, accuracy_m=17.0)
             for i, t in enumerate(range(0, 305, 5))]
    enc, _ = compute(fixes, [cand("blurry", 210, 12)])
    assert judge(enc[0], band=15) == "unjudgeable"   # 오차 17m 로 15m 원 판정은 소음
    assert judge(enc[0], band=20) != "unjudgeable"


def test_judgment_does_not_treat_legacy_aggregate_as_one_occurrence():
    enc, _ = compute(straight_walk(300), [cand("legacy", 210, 12)])
    legacy = enc[0].model_copy(update={"occurrence_version": 1, "pass_count": 2})

    assert JUDGMENT_VERSION == 3
    assert judge(legacy) == "unjudgeable"
