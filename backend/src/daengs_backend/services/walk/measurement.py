"""산책 계산이 무엇을 보고 무엇을 버렸는지 원시 수치로 고정한다."""

import math
from collections.abc import Sequence

from daengs_backend.services.walk.contracts import (
    MeasurementReceipt,
    WalkEvidencePoint,
)
from daengs_backend.services.walk.facts import ComputedWalkFacts


def _percentile(ordered: list[float], quantile: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] * (high - position) + ordered[high] * (position - low)


def _accuracy_summary(
    points: Sequence[WalkEvidencePoint],
) -> tuple[int, float | None, float | None]:
    values = sorted(float(point.accuracy_m) for point in points if point.accuracy_m is not None)
    if not values:
        return 0, None, None
    return (
        len(values),
        round(_percentile(values, 0.50), 2),
        round(_percentile(values, 0.90), 2),
    )


def build_measurement_receipt(
    computed: ComputedWalkFacts,
    received_points: Sequence[WalkEvidencePoint],
) -> MeasurementReceipt:
    """비율이나 신뢰도 점수 없이 이름 붙은 분자·분모만 만든다."""

    facts = computed.facts
    quality = computed.quality
    reported_count, reported_p50, reported_p90 = _accuracy_summary(received_points)
    accepted_count, accepted_p50, accepted_p90 = _accuracy_summary(computed.accepted_points)
    return MeasurementReceipt(
        walk_id=facts.walk_id,
        evidence_origin=facts.evidence_origin,
        received_fix_count=quality.received,
        accepted_fix_count=quality.accepted,
        rejected_low_accuracy_count=quality.rejected_low_accuracy,
        rejected_out_of_order_count=quality.rejected_out_of_order,
        rejected_before_start_count=quality.rejected_before_start,
        rejected_after_end_count=quality.rejected_after_end,
        unknown_accuracy_count=quality.unknown_accuracy,
        jump_break_count=quality.jump_breaks,
        gap_break_count=quality.gap_breaks,
        explicit_break_count=quality.explicit_breaks,
        dropped_at_capacity_count=quality.dropped_at_capacity,
        mock_fix_count=quality.mock_fixes,
        session_wall_time_s=(facts.ended_at - facts.started_at).total_seconds(),
        canonical_segment_time_s=math.fsum(segment.dt for segment in computed.segments),
        gap_elapsed_s=math.fsum(gap.dt for gap in computed.gaps),
        reported_accuracy_count=reported_count,
        reported_accuracy_p50_m=reported_p50,
        reported_accuracy_p90_m=reported_p90,
        accepted_accuracy_count=accepted_count,
        accepted_accuracy_p50_m=accepted_p50,
        accepted_accuracy_p90_m=accepted_p90,
    )
