"""무좌표 진행 곡선. `WalkFacts` 평균 하나로는 못 하는 구분을 남기는지 본다.

곡선을 남기는 이유가 "평균이 같고 모양이 다른 두 산책을 가른다" 이므로, 여기 단언도 전부
**같은 평균에서 다른 모양이 나오는가** 로 쓴다.
"""

from datetime import UTC, datetime, timedelta

from app.features.walk.curve import BUCKETS, CurveBucket, compute_curve
from app.features.walk.facts import Segment
from app.features.walk.models import WalkFix

T0 = datetime(2026, 8, 25, 9, 0, tzinfo=UTC)


def _fix(second: int) -> WalkFix:
    """좌표는 곡선에 안 들어가므로 고정값이면 된다."""
    return WalkFix(client_seq=second, at=T0 + timedelta(seconds=second), lat=37.5, lng=127.0)


def _walk(speeds: list[float], *, step_s: int = 10) -> tuple[datetime, list[Segment]]:
    """구간마다 지정한 속도로 걷는 산책. 속도 0 은 정지 후보다."""
    segments, offset = [], 0.0
    for i, mps in enumerate(speeds):
        a, b = _fix(i * step_s), _fix((i + 1) * step_s)
        dist = mps * step_s
        segments.append(Segment(a=a, b=b, dt=float(step_s), dist=dist, offset_m=offset,
                                moving=mps > 0, chain_index=0))
        offset += dist
    return T0 + timedelta(seconds=len(speeds) * step_s), segments


def _speed(bucket: CurveBucket) -> float | None:
    return bucket.moving_m / bucket.moving_s if bucket.moving_s else None


def test_a_flat_walk_and_a_fading_walk_have_the_same_average_but_different_curves():
    """이 테스트가 곡선의 존재 이유다 — `avg_speed_mps` 로는 둘이 같은 값이 된다."""
    flat_end, flat = _walk([1.0] * 20)
    fade_end, fade = _walk([1.5] * 10 + [0.5] * 10)

    assert sum(s.dist for s in flat) == sum(s.dist for s in fade), "평균이 같아야 비교가 성립한다"

    flat_curve = compute_curve(T0, flat_end, flat)
    fade_curve = compute_curve(T0, fade_end, fade)

    assert _speed(flat_curve[0]) == _speed(flat_curve[-1]), "평탄한 산책은 앞뒤가 같다"
    assert _speed(fade_curve[0]) > _speed(fade_curve[-1]) * 2, "후반 감속이 곡선에 보여야 한다"


def test_every_bucket_has_a_slot_even_when_nothing_happened_there():
    """빈 구간도 자리가 있어야 두 산책의 같은 구간을 바로 비교할 수 있다."""
    end, segments = _walk([1.0, 1.0])          # 20초짜리 — 대부분의 버킷이 빈다

    curve = compute_curve(T0, end, segments)

    assert [b.index for b in curve] == list(range(BUCKETS))
    assert sum(b.moving_s for b in curve) == 20.0, "구간에 나눠 담아도 총합은 보존된다"


def test_stops_land_in_the_bucket_where_they_happened():
    """정지가 어느 구간에 몰렸나 — `stop_count` 총합으로는 답할 수 없는 질문이다."""
    end, segments = _walk([1.0] * 15 + [0.0] * 5)

    curve = compute_curve(T0, end, segments)

    assert curve[0].still_s == 0.0
    assert curve[-1].still_s > 0.0, "마지막 구간의 정지가 안 잡혔다"
    assert sum(b.still_s for b in curve) == 50.0


def test_a_zero_length_session_does_not_divide_by_zero():
    _, segments = _walk([1.0])

    curve = compute_curve(T0, T0, segments)          # started_at == ended_at

    assert len(curve) == BUCKETS
    assert curve[0].moving_s == 10.0


def test_the_curve_carries_no_coordinates():
    """결정 #57 의 무좌표 층에 들어가는 근거. 필드가 늘면 여기서 걸린다."""
    end, segments = _walk([1.0, 0.0, 1.0])

    keys = set(compute_curve(T0, end, segments)[0].to_dict())

    assert keys == {"index", "moving_s", "moving_m", "still_s"}
