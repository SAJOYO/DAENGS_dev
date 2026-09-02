"""산책 측정 커널의 단일 공개 진입점."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from daengs_walk.contracts import (
    CanonicalWalkFacts,
    FixQualityCounts,
    MeasurementReceipt,
    MicroObservation,
    MotionEventOccurrence,
    MovingSpeedProfile,
    WalkEvidencePoint,
)
from daengs_walk.facts import (
    CanonicalSegment,
    GapSpan,
    compute_walk_facts,
)
from daengs_walk.measurement import build_measurement_receipt
from daengs_walk.observation import (
    extract_micro_observations,
    moving_speed_profile,
)


@dataclass(frozen=True)
class WalkEvidenceBundle:
    facts: CanonicalWalkFacts
    quality: FixQualityCounts
    events: tuple[MotionEventOccurrence, ...]
    segments: tuple[CanonicalSegment, ...]
    gaps: tuple[GapSpan, ...]
    observations: tuple[MicroObservation, ...]
    speed_profile: MovingSpeedProfile | None
    receipt: MeasurementReceipt


def analyze_walk(
    walk_id: uuid.UUID,
    started_at: datetime,
    ended_at: datetime,
    points: Sequence[WalkEvidencePoint],
) -> WalkEvidenceBundle:
    """한 번의 정렬·계산으로 다음 Cellophane·Capsule 단계의 입력을 만든다."""

    computed = compute_walk_facts(walk_id, started_at, ended_at, points)
    observations = extract_micro_observations(
        walk_id,
        computed.segments,
        computed.gaps,
    )
    return WalkEvidenceBundle(
        facts=computed.facts,
        quality=computed.quality,
        events=computed.events,
        segments=computed.segments,
        gaps=computed.gaps,
        observations=observations,
        speed_profile=moving_speed_profile(computed.segments),
        receipt=build_measurement_receipt(computed, points),
    )
