"""canonical 구간에서 판정 이전의 저속·관측 공백 후보를 남긴다."""

import math
import statistics
import uuid
from collections.abc import Sequence
from datetime import UTC

from daengs_walk.contracts import (
    MicroObservation,
    MovingSpeedProfile,
    WalkEvidencePoint,
)
from daengs_walk.facts import CanonicalSegment, GapSpan, haversine_m

CANDIDATE_SPEED_MPS = 1.0
CANDIDATE_MIN_S = 3.0
MIN_SPEED_SAMPLES = 5


def _percentile(ordered: list[float], quantile: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] * (high - position) + ordered[high] * (position - low)


def moving_speed_profile(
    segments: Sequence[CanonicalSegment],
) -> MovingSpeedProfile | None:
    speeds = sorted(
        segment.dist / segment.dt for segment in segments if segment.moving and segment.dt > 0
    )
    if len(speeds) < MIN_SPEED_SAMPLES:
        return None
    return MovingSpeedProfile(
        p50=_percentile(speeds, 0.50),
        p70=_percentile(speeds, 0.70),
        p80=_percentile(speeds, 0.80),
        p90=_percentile(speeds, 0.90),
        sample_n=len(speeds),
    )


def _slow_window(
    walk_id: uuid.UUID,
    run: list[CanonicalSegment],
    *,
    abuts_break: bool,
) -> MicroObservation:
    points: list[WalkEvidencePoint] = [run[0].a] + [segment.b for segment in run]
    duration = math.fsum(segment.dt for segment in run)
    path = math.fsum(segment.dist for segment in run)
    lat = math.fsum(point.lat for point in points) / len(points)
    lng = math.fsum(point.lng for point in points) / len(points)
    centre = WalkEvidencePoint(
        client_seq=0,
        at=run[0].a.at,
        lat=lat,
        lng=lng,
    )
    accuracies = [point.accuracy_m for point in points if point.accuracy_m is not None]
    return MicroObservation(
        walk_id=walk_id,
        index=0,
        kind="slow",
        started_at=run[0].a.at.astimezone(UTC),
        ended_at=run[-1].b.at.astimezone(UTC),
        duration_s=duration,
        lat=lat,
        lng=lng,
        path_m=path,
        net_m=haversine_m(run[0].a, run[-1].b),
        span_m=max(haversine_m(centre, point) for point in points),
        fix_count=len(points),
        accuracy_p50_m=(round(statistics.median(accuracies), 2) if accuracies else None),
        route_offset_m=run[0].offset_m,
        chain_index=run[0].chain_index,
        abuts_break=abuts_break,
    )


def _gap_observation(walk_id: uuid.UUID, gap: GapSpan) -> MicroObservation:
    accuracies = [point.accuracy_m for point in (gap.a, gap.b) if point.accuracy_m is not None]
    return MicroObservation(
        walk_id=walk_id,
        index=0,
        kind="gap",
        started_at=gap.a.at.astimezone(UTC),
        ended_at=gap.b.at.astimezone(UTC),
        duration_s=gap.dt,
        lat=gap.a.lat,
        lng=gap.a.lng,
        path_m=0,
        net_m=haversine_m(gap.a, gap.b),
        span_m=0,
        fix_count=2,
        accuracy_p50_m=(round(statistics.median(accuracies), 2) if accuracies else None),
        route_offset_m=gap.offset_m,
        chain_index=gap.chain_index,
        abuts_break=True,
    )


def _touches_edge(
    run: list[CanonicalSegment],
    start_index: int,
    end_index: int,
    bounds: dict[int, tuple[int, int]],
) -> bool:
    first, last = bounds[run[0].chain_index]
    return start_index == first or end_index == last


def extract_micro_observations(
    walk_id: uuid.UUID,
    segments: Sequence[CanonicalSegment],
    gaps: Sequence[GapSpan] = (),
) -> tuple[MicroObservation, ...]:
    """1m/s 아래의 연속 창과 비관측 gap을 서로 다른 종류로 만든다."""

    runs: list[tuple[list[CanonicalSegment], bool]] = []
    chain_bounds: dict[int, tuple[int, int]] = {}
    for index, segment in enumerate(segments):
        first, _ = chain_bounds.get(segment.chain_index, (index, index))
        chain_bounds[segment.chain_index] = (first, index)

    run: list[CanonicalSegment] = []
    start_index = 0
    for index, segment in enumerate(segments):
        slow = segment.dt > 0 and segment.dist / segment.dt < CANDIDATE_SPEED_MPS
        contiguous = bool(run) and segment.chain_index == run[-1].chain_index
        if slow and (contiguous or not run):
            if not run:
                start_index = index
            run.append(segment)
            continue
        if run:
            runs.append(
                (
                    run,
                    _touches_edge(run, start_index, index - 1, chain_bounds),
                )
            )
            run = []
        if slow:
            start_index = index
            run = [segment]
    if run:
        runs.append(
            (
                run,
                _touches_edge(run, start_index, len(segments) - 1, chain_bounds),
            )
        )

    observations = [
        _slow_window(walk_id, candidate, abuts_break=abuts_break)
        for candidate, abuts_break in runs
        if math.fsum(segment.dt for segment in candidate) >= CANDIDATE_MIN_S
    ]
    observations.extend(_gap_observation(walk_id, gap) for gap in gaps)
    observations.sort(key=lambda observation: (observation.started_at, observation.kind))
    return tuple(
        observation.model_copy(update={"index": index})
        for index, observation in enumerate(observations)
    )
