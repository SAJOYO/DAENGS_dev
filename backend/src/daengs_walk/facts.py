"""원본 좌표열을 canonical 산책 사실로 바꾸는 결정론적 계산."""

import math
import statistics
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from daengs_walk.contracts import (
    CanonicalWalkFacts,
    FixQualityCounts,
    MotionEventOccurrence,
    WalkEvidencePoint,
)

MOVING_SPEED_MPS = 0.5
MIN_STOP_S = 10.0
MAX_ACCURACY_M = 50.0
MAX_JUMP_M = 200.0
MAX_GAP_S = 60.0
MAX_SAMPLES = 20_000


@dataclass(frozen=True)
class CanonicalSegment:
    a: WalkEvidencePoint
    b: WalkEvidencePoint
    dt: float
    dist: float
    offset_m: float
    moving: bool
    chain_index: int


@dataclass(frozen=True)
class GapSpan:
    """관측이 끊긴 시간. 그 사이에 머물렀다고 주장하지 않는다."""

    a: WalkEvidencePoint
    b: WalkEvidencePoint
    dt: float
    offset_m: float
    chain_index: int


@dataclass(frozen=True)
class ComputedWalkFacts:
    facts: CanonicalWalkFacts
    quality: FixQualityCounts
    events: tuple[MotionEventOccurrence, ...] = ()
    segments: tuple[CanonicalSegment, ...] = ()
    gaps: tuple[GapSpan, ...] = ()
    # MeasurementReceipt를 만들 때만 쓴다. 영속 계약에는 포함하지 않는다.
    accepted_points: tuple[WalkEvidencePoint, ...] = ()


@dataclass
class _MutableQuality:
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

    def freeze(self) -> FixQualityCounts:
        return FixQualityCounts(**vars(self))


def haversine_m(a: WalkEvidencePoint, b: WalkEvidencePoint) -> float:
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat = lat2 - lat1
    dlng = math.radians(b.lng - a.lng)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def _ordered_points(points: Sequence[WalkEvidencePoint]) -> list[WalkEvidencePoint]:
    ordered = sorted(points, key=lambda point: point.client_seq)
    seqs = [point.client_seq for point in ordered]
    if len(seqs) != len(set(seqs)):
        raise ValueError("client_seq must be unique within a walk")
    return ordered


def _require_session_time(started_at: datetime, ended_at: datetime) -> None:
    for value in (started_at, ended_at):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("walk timestamps must include a timezone")
    if ended_at < started_at:
        raise ValueError("ended_at must not precede started_at")


def compute_walk_facts(
    walk_id: uuid.UUID,
    started_at: datetime,
    ended_at: datetime,
    points: Sequence[WalkEvidencePoint],
) -> ComputedWalkFacts:
    """저장 순서와 무관하게 ``client_seq`` 순으로 같은 사실을 만든다."""

    _require_session_time(started_at, ended_at)
    ordered = _ordered_points(points)
    quality = _MutableQuality(
        received=len(ordered),
        mock_fixes=sum(1 for point in ordered if point.is_mock),
    )
    origins = {point.is_mock for point in ordered}
    evidence_origin = (
        "unknown"
        if not origins
        else "mixed"
        if len(origins) > 1
        else "mock"
        if True in origins
        else "device"
    )

    duration = 0.0
    distance = 0.0
    moving_s = 0.0
    moving_distance = 0.0
    still_run = 0.0
    still_points: list[WalkEvidencePoint] = []
    still_offset_m = 0.0
    events: list[MotionEventOccurrence] = []
    segments: list[CanonicalSegment] = []
    gaps: list[GapSpan] = []
    accepted_points: list[WalkEvidencePoint] = []
    canonical_chain = 0

    def break_chain() -> None:
        nonlocal canonical_chain
        canonical_chain += 1

    def close_still_run() -> None:
        nonlocal still_run, still_points
        if still_run >= MIN_STOP_S and len(still_points) >= 2:
            accuracies = [
                point.accuracy_m for point in still_points if point.accuracy_m is not None
            ]
            events.append(
                MotionEventOccurrence(
                    walk_id=walk_id,
                    event_index=len(events),
                    started_at=still_points[0].at.astimezone(UTC),
                    ended_at=still_points[-1].at.astimezone(UTC),
                    duration_s=round(still_run),
                    lat=sum(point.lat for point in still_points) / len(still_points),
                    lng=sum(point.lng for point in still_points) / len(still_points),
                    route_offset_m=round(still_offset_m, 3),
                    accuracy_p50_m=(
                        round(statistics.median(accuracies), 2) if accuracies else None
                    ),
                    fix_count=len(still_points),
                )
            )
        still_run = 0.0
        still_points = []

    previous: WalkEvidencePoint | None = None
    for current in ordered:
        if quality.accepted >= MAX_SAMPLES:
            quality.dropped_at_capacity += 1
            continue
        if current.at < started_at:
            quality.rejected_before_start += 1
            close_still_run()
            break_chain()
            previous = None
            continue
        if current.at > ended_at:
            quality.rejected_after_end += 1
            close_still_run()
            break_chain()
            previous = None
            continue
        if current.accuracy_m is None:
            quality.unknown_accuracy += 1
        elif current.accuracy_m > MAX_ACCURACY_M:
            quality.rejected_low_accuracy += 1
            close_still_run()
            break_chain()
            previous = None
            continue

        quality.accepted += 1
        accepted_points.append(current)
        if previous is None:
            previous = current
            continue
        if current.chain_index != previous.chain_index:
            quality.explicit_breaks += 1
            close_still_run()
            break_chain()
            previous = current
            continue

        dt = (current.at - previous.at).total_seconds()
        if dt <= 0:
            quality.rejected_out_of_order += 1
            close_still_run()
            break_chain()
            previous = current
            continue
        if dt > MAX_GAP_S:
            quality.gap_breaks += 1
            gaps.append(
                GapSpan(
                    a=previous,
                    b=current,
                    dt=dt,
                    offset_m=moving_distance,
                    chain_index=canonical_chain,
                )
            )
            close_still_run()
            break_chain()
            previous = current
            continue

        dist = haversine_m(previous, current)
        if dist > MAX_JUMP_M:
            quality.jump_breaks += 1
            close_still_run()
            break_chain()
            previous = current
            continue

        moving = dist / dt >= MOVING_SPEED_MPS
        duration += dt
        distance += dist
        segments.append(
            CanonicalSegment(
                a=previous,
                b=current,
                dt=dt,
                dist=dist,
                offset_m=moving_distance,
                moving=moving,
                chain_index=canonical_chain,
            )
        )
        if moving:
            close_still_run()
            moving_s += dt
            moving_distance += dist
        else:
            if not still_points:
                still_points = [previous]
                still_offset_m = moving_distance
            still_points.append(current)
            still_run += dt
        previous = current
    close_still_run()

    moving_distance = min(moving_distance, distance)
    stop_s = sum(event.duration_s for event in events)
    facts = CanonicalWalkFacts(
        walk_id=walk_id,
        evidence_origin=evidence_origin,
        started_at=started_at.astimezone(UTC),
        ended_at=ended_at.astimezone(UTC),
        duration_s=round(duration),
        distance_m=round(distance),
        moving_distance_m=round(moving_distance),
        moving_s=round(moving_s),
        stop_count=len(events),
        stop_s=min(round(stop_s), max(round(duration) - round(moving_s), 0)),
        avg_speed_mps=(round(moving_distance / moving_s, 3) if moving_s > 0 else None),
        fix_count=quality.accepted,
    )
    return ComputedWalkFacts(
        facts=facts,
        quality=quality.freeze(),
        events=tuple(events),
        segments=tuple(segments),
        gaps=tuple(gaps),
        accepted_points=tuple(accepted_points),
    )
