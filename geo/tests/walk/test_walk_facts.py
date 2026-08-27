"""사실 계산의 성질 고정 — 같은 입력은 같은 사실, 걸음/정지/거부의 경계.

전부 순수함수 테스트다. DB 없음. 좌표는 동해 원점에서 동쪽 미터로 만든다.
"""

from datetime import timedelta

from app.features.walk.facts import compute_facts
from app.features.walk.models import WalkFix
from tests.conftest import WALK_T0, walk_fix


def walk_then_stop_then_walk() -> list[WalkFix]:
    """5초 간격. 0~60초 걷기(7m/5s=1.4m/s) → 60~90초 제자리 → 90~150초 걷기."""
    fixes = [walk_fix(t, t / 5 * 7) for t in range(0, 65, 5)]            # 0..60s, 0..84m
    fixes += [walk_fix(t, 84) for t in range(65, 90, 5)]                 # 정지
    fixes += [walk_fix(t, 84 + (t - 90) / 5 * 7) for t in range(90, 155, 5)]
    return fixes


def compute(fixes: list[WalkFix], ended_s: float = 150):
    return compute_facts("s1", "halmae", WALK_T0, WALK_T0 + timedelta(seconds=ended_s), fixes)


def test_same_input_same_facts():
    a = compute(walk_then_stop_then_walk()).facts
    b = compute(walk_then_stop_then_walk()).facts
    assert a.model_dump() == b.model_dump()


def test_walk_stop_walk_boundaries():
    r = compute(walk_then_stop_then_walk())
    f = r.facts
    assert f.stop_count == 1
    assert f.stop_s >= 25                       # 60~90초 정지 (마지막 5초는 걷기 쌍에 걸침)
    assert f.moving_s + f.stop_s <= f.duration_s
    assert 150 <= f.moving_distance_m <= f.distance_m <= 180
    assert f.avg_speed_mps and 1.3 <= f.avg_speed_mps <= 1.5


def test_low_accuracy_is_rejected_unknown_is_not():
    fixes = [walk_fix(0, 0), walk_fix(5, 7, accuracy=80.0), walk_fix(10, 14, accuracy=None), walk_fix(15, 21)]
    r = compute(fixes, 15)
    assert r.quality.rejected_low_accuracy == 1
    assert r.quality.unknown_accuracy == 1
    assert r.facts.fix_count == 3               # 80m 짜리만 빠졌다


def test_jump_breaks_do_not_accumulate():
    fixes = [walk_fix(0, 0), walk_fix(5, 7), walk_fix(10, 500), walk_fix(15, 507)]      # 10초에 493m 튐
    r = compute(fixes, 15)
    assert r.quality.jump_breaks == 1
    assert r.facts.distance_m == 14             # 7m + (튐 제외) + 7m
    assert r.facts.duration_s == 10             # 튐 구간의 5초는 어디에도 없다


def test_time_going_backwards_is_rejected():
    fixes = [walk_fix(0, 0), walk_fix(5, 7)]
    fixes.append(WalkFix(client_seq=6000, at=fixes[1].at, lat=fixes[1].lat,
                         lng=fixes[1].lng, accuracy_m=10.0))  # 같은 시각의 별도 관측
    r = compute(fixes, 10)
    assert r.quality.rejected_out_of_order == 1
    assert r.facts.distance_m == 7


def test_fewer_than_two_fixes_yields_zero_facts():
    r = compute([walk_fix(0, 0)], 60)
    f = r.facts
    assert (f.duration_s, f.distance_m, f.moving_s, f.stop_count) == (0, 0, 0, 0)
    assert f.avg_speed_mps is None


def test_short_pause_is_not_a_stop():
    """10초 미만 정지 후보는 stop 이 아니다 — 신호 대기와 지터를 가르는 최소선."""
    fixes = [walk_fix(0, 0), walk_fix(5, 7), walk_fix(10, 7), walk_fix(15, 14)]         # 5초만 멈춤
    f = compute(fixes, 15).facts
    assert f.stop_count == 0
    assert f.moving_s + f.stop_s <= f.duration_s


def test_collection_gap_is_not_a_stop():
    r = compute([walk_fix(0, 0), walk_fix(600, 0)], 600)
    assert r.quality.gap_breaks == 1
    assert r.facts.stop_count == 0
    assert r.facts.stop_s == 0


def test_pause_resume_chain_is_never_joined_as_movement():
    """Pause 중 80m 이동 후 재개해도 그 공백은 산책 거리나 segment가 아니다."""
    r = compute([
        walk_fix(0, 0, chain_index=0),
        walk_fix(5, 7, chain_index=0),
        walk_fix(35, 87, chain_index=1),
        walk_fix(40, 94, chain_index=1),
    ], 40)
    assert r.quality.explicit_breaks == 1
    assert r.facts.distance_m == 14
    assert r.facts.moving_s == 10
    assert len(r.segments) == 2
    assert [s.chain_index for s in r.segments] == [0, 1]


def test_fixes_outside_session_are_rejected_and_break_segments():
    fixes = [walk_fix(-60, 0), walk_fix(0, 0), walk_fix(60, 10), walk_fix(120, 20)]
    r = compute(fixes, 60)
    assert r.quality.rejected_before_start == 1
    assert r.quality.rejected_after_end == 1
    assert r.facts.duration_s == 60


def test_mock_origin_is_visible_in_facts_and_quality():
    r = compute([walk_fix(0, 0, is_mock=True), walk_fix(5, 7, is_mock=True)], 5)
    assert r.facts.evidence_origin == "mock"
    assert r.quality.mock_fixes == 2


def test_stop_event_keeps_time_and_spatial_anchor():
    r = compute(walk_then_stop_then_walk())
    assert len(r.events) == 1
    event = r.events[0]
    assert event.type == "stop"
    assert event.duration_s == r.facts.stop_s
    assert event.started_at < event.ended_at
    assert abs(event.lng - walk_fix(60, 84).lng) < 1e-8
    assert event.route_offset_m > 70
