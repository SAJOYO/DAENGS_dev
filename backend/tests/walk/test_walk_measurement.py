from datetime import timedelta

import pytest
from pydantic import ValidationError

from daengs_backend.services.walk import analyze_walk
from daengs_backend.services.walk.contracts import MeasurementReceipt
from daengs_backend.services.walk.facts import compute_walk_facts
from daengs_backend.services.walk.measurement import build_measurement_receipt


def test_receipt_preserves_denominators_and_both_accuracy_distributions(walk_id, started_at, point):
    points = [
        point(0, 0, accuracy=10),
        point(5, 7, accuracy=80),
        point(10, 14, accuracy=None),
        point(15, 21, accuracy=20),
    ]
    computed = compute_walk_facts(
        walk_id,
        started_at,
        started_at + timedelta(seconds=20),
        points,
    )
    receipt = build_measurement_receipt(computed, points)

    assert (receipt.received_fix_count, receipt.accepted_fix_count) == (4, 3)
    assert receipt.rejected_low_accuracy_count == 1
    assert receipt.unknown_accuracy_count == 1
    assert receipt.session_wall_time_s == 20
    assert receipt.canonical_segment_time_s == 5
    assert receipt.reported_accuracy_count == 3
    assert (receipt.reported_accuracy_p50_m, receipt.reported_accuracy_p90_m) == (
        20,
        68,
    )
    assert receipt.accepted_accuracy_count == 2
    assert (receipt.accepted_accuracy_p50_m, receipt.accepted_accuracy_p90_m) == (
        15,
        19,
    )


def test_receipt_has_no_confidence_score_and_is_frozen(walk_id, started_at, point):
    bundle = analyze_walk(
        walk_id,
        started_at,
        started_at + timedelta(seconds=5),
        [point(0, 0), point(5, 7)],
    )

    assert "confidence" not in MeasurementReceipt.model_fields
    assert "accepted_ratio" not in MeasurementReceipt.model_fields
    with pytest.raises(ValidationError):
        bundle.receipt.accepted_fix_count = 0


def test_break_counters_are_not_a_partition_of_received_points(walk_id, started_at, point):
    points = [point(0, 0), point(120, 1), point(125, 8)]
    bundle = analyze_walk(
        walk_id,
        started_at,
        started_at + timedelta(seconds=125),
        points,
    )

    # 세 점은 모두 point-level 수용 조건을 통과하지만 첫 쌍은 gap이라 구간이 아니다.
    assert bundle.receipt.accepted_fix_count == 3
    assert bundle.receipt.gap_break_count == 1
    assert bundle.receipt.canonical_segment_time_s == 5


def test_analyze_walk_is_the_single_deterministic_entrypoint(walk_id, started_at, point):
    points = [point(t, t / 5 * 4) for t in range(0, 31, 5)]
    first = analyze_walk(
        walk_id,
        started_at,
        started_at + timedelta(seconds=30),
        points,
    )
    second = analyze_walk(
        walk_id,
        started_at,
        started_at + timedelta(seconds=30),
        list(reversed(points)),
    )

    assert first == second
    assert first.facts.walk_id == walk_id
    assert first.receipt.walk_id == walk_id
    assert first.observations
