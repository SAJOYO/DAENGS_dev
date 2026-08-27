"""WalkFix 열 → WalkFacts. 순수함수 — DB·시계·난수 없음, 같은 입력은 같은 사실.

계산 정책 v4. 문턱값은 실기기 반복 측정 전의 잠정값이며 계산 버전으로 결과에 남긴다.
Android 미리보기와 서버 확정치를 맞추는 작업은 앱 수집기가 이 계약을 채택할 때 한다.

분류 규칙 (연속한 수용 점 쌍마다):
  dt <= 0            → out_of_order 거부. 시각 역행을 이동으로 만들지 않는다
  chain 변경         → 명시적 단절. pause 중 이동을 가상의 segment로 만들지 않는다
  dt > 60s           → gap. 수집 공백을 정지로 만들지 않는다
  dist > 200m        → jump. 구간 단절 — 시간·거리 어디에도 안 쌓는다 (GPS 튐/차량)
  speed >= 0.5 m/s   → moving:  moving_s += dt, moving_distance += dist
  speed <  0.5 m/s   → 정지 후보: 원 distance 에만 쌓인다 (지터 포함 참고값)
정지 후보가 10초 이상 이어지면 stop 하나다. "정지"까지만 — 이유는 붙이지 않는다.
"""

import math
import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.features.walk.models import (
    CALCULATION_VERSION,
    MotionEventOccurrence,
    WalkFacts,
    WalkFix,
)

MOVING_SPEED_MPS = 0.5
MIN_STOP_S = 10.0
MAX_ACCURACY_M = 50.0
MAX_JUMP_M = 200.0
MAX_GAP_S = 60.0
MAX_SAMPLES = 20_000


@dataclass
class FixQuality:
    """수신 원본이 사실이 되기까지 어디서 얼마나 걸러졌나. 계약 밖 — 운영·디버깅용."""

    received: int = 0
    accepted: int = 0
    rejected_low_accuracy: int = 0
    rejected_out_of_order: int = 0
    rejected_before_start: int = 0
    rejected_after_end: int = 0
    unknown_accuracy: int = 0
    jump_breaks: int = 0
    gap_breaks: int = 0
    explicit_breaks: int = 0
    dropped_at_capacity: int = 0
    mock_fixes: int = 0

    def to_dict(self) -> dict:
        return dict(vars(self))


@dataclass(frozen=True)
class Segment:
    """수용된 연속 두 점 사이의 유효 구간. encounter 계산이 같은 진실을 쓴다.

    offset_m 은 구간 시작 시점의 이동거리(moving_distance) — 이벤트의 route_offset 과 같은 자."""

    a: WalkFix
    b: WalkFix
    dt: float
    dist: float
    offset_m: float
    moving: bool
    chain_index: int


@dataclass
class ComputedFacts:
    facts: WalkFacts
    quality: FixQuality = field(default_factory=FixQuality)
    events: list[MotionEventOccurrence] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)


def _haversine_m(a: WalkFix, b: WalkFix) -> float:
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat = lat2 - lat1
    dlng = math.radians(b.lng - a.lng)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def compute_facts(
    session_id: str,
    dog_id: str,
    started_at: datetime,
    ended_at: datetime,
    fixes: list[WalkFix],
) -> ComputedFacts:
    """fixes는 client_seq 순서다. 점이 2개 미만이면 전부 0인 사실이 나온다."""
    q = FixQuality(received=len(fixes), mock_fixes=sum(1 for f in fixes if f.is_mock))
    origins = {f.is_mock for f in fixes}
    evidence_origin = (
        "unknown" if not origins else "mixed" if len(origins) > 1 else "mock" if True in origins
        else "device"
    )

    duration = 0.0
    distance = 0.0
    moving_s = 0.0
    moving_distance = 0.0
    still_run = 0.0                      # 진행 중인 정지 후보 구간의 누적 초
    still_points: list[WalkFix] = []
    still_offset_m = 0.0
    events: list[MotionEventOccurrence] = []
    segments: list[Segment] = []
    chain_index = 0

    def break_chain() -> None:
        """다음 유효 segment를 새 연속열로 시작한다.

        gap/jump/거부 지점 너머는 공간적으로 이어 보이면 안 된다. encounter 같은
        소비자가 이 경계를 보지 못하면 수집 공백 양쪽의 원 진입을 한 번으로 합친다.
        """
        nonlocal chain_index
        chain_index += 1

    def close_still_run() -> None:
        nonlocal still_run, still_points
        if still_run >= MIN_STOP_S and len(still_points) >= 2:
            accuracies = [f.accuracy_m for f in still_points if f.accuracy_m is not None]
            events.append(MotionEventOccurrence(
                session_id=session_id,
                event_index=len(events),
                started_at=still_points[0].at.astimezone(UTC),
                ended_at=still_points[-1].at.astimezone(UTC),
                duration_s=round(still_run),
                lat=sum(f.lat for f in still_points) / len(still_points),
                lng=sum(f.lng for f in still_points) / len(still_points),
                route_offset_m=round(still_offset_m, 3),
                accuracy_p50_m=round(statistics.median(accuracies), 2) if accuracies else None,
                fix_count=len(still_points),
            ))
        still_run = 0.0
        still_points = []

    prev: WalkFix | None = None
    for cur in fixes:
        if q.accepted >= MAX_SAMPLES:
            q.dropped_at_capacity += 1
            continue
        if cur.at < started_at:
            q.rejected_before_start += 1
            close_still_run()
            break_chain()
            prev = None
            continue
        if cur.at > ended_at:
            q.rejected_after_end += 1
            close_still_run()
            break_chain()
            prev = None
            continue
        if cur.accuracy_m is None:
            q.unknown_accuracy += 1      # 모름은 거부 사유가 아니다 — 세되 받는다
        elif cur.accuracy_m > MAX_ACCURACY_M:
            q.rejected_low_accuracy += 1
            close_still_run()
            break_chain()
            prev = None                  # 거부 지점 양쪽을 가상의 직선으로 잇지 않는다
            continue
        q.accepted += 1
        if prev is None:
            prev = cur
            continue
        if cur.chain_index != prev.chain_index:
            q.explicit_breaks += 1
            close_still_run()
            break_chain()
            prev = cur
            continue
        dt = (cur.at - prev.at).total_seconds()
        if dt <= 0:
            q.rejected_out_of_order += 1
            close_still_run()
            break_chain()
            prev = cur                   # 역행 지점에서 segment를 끊고 새로 시작
            continue
        if dt > MAX_GAP_S:
            q.gap_breaks += 1
            close_still_run()
            break_chain()
            prev = cur
            continue
        dist = _haversine_m(prev, cur)
        if dist > MAX_JUMP_M:
            q.jump_breaks += 1
            close_still_run()            # 단절 너머로 정지 구간을 잇지 않는다
            break_chain()
            prev = cur
            continue
        duration += dt
        distance += dist
        segments.append(Segment(a=prev, b=cur, dt=dt, dist=dist,
                                offset_m=moving_distance,
                                moving=dist / dt >= MOVING_SPEED_MPS,
                                chain_index=chain_index))
        if dist / dt >= MOVING_SPEED_MPS:
            close_still_run()
            moving_s += dt
            moving_distance += dist
        else:
            if not still_points:
                still_points = [prev]
                still_offset_m = moving_distance
            still_points.append(cur)
            still_run += dt
        prev = cur
    close_still_run()

    moving_distance = min(moving_distance, distance)
    stop_s = sum(e.duration_s for e in events)
    facts = WalkFacts(
        calculation_version=CALCULATION_VERSION,
        session_id=session_id,
        dog_id=dog_id,
        evidence_origin=evidence_origin,
        # UTC 정규화 — 같은 순간의 +09:00 표기와 Z 표기가 다른 사실처럼 보이면 안 된다.
        # 멱등 재요청은 저장본과 바이트까지 같은 응답을 받는다.
        started_at=started_at.astimezone(UTC),
        ended_at=ended_at.astimezone(UTC),
        duration_s=round(duration),
        distance_m=round(distance),
        moving_distance_m=round(moving_distance),
        moving_s=round(moving_s),
        stop_count=len(events),
        stop_s=min(round(stop_s), max(round(duration) - round(moving_s), 0)),
        avg_speed_mps=round(moving_distance / moving_s, 3) if moving_s > 0 else None,
        fix_count=q.accepted,
    )
    return ComputedFacts(facts=facts, quality=q, events=events, segments=segments)
