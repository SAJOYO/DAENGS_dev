import uuid
from datetime import datetime, timedelta

import pytest

from daengs_walk import WalkEvidencePoint
from daengs_walk.facts import compute_walk_facts


def _compute(walk_id, started_at, points, ended_s=150):
    return compute_walk_facts(
        walk_id,
        started_at,
        started_at + timedelta(seconds=ended_s),
        points,
    )


def _walk_stop_walk(point):
    points = [point(t, t / 5 * 7) for t in range(0, 65, 5)]
    points += [point(t, 84) for t in range(65, 90, 5)]
    points += [point(t, 84 + (t - 90) / 5 * 7) for t in range(90, 155, 5)]
    return points


def test_same_points_in_different_upload_order_make_the_same_facts(walk_id, started_at, point):
    points = _walk_stop_walk(point)
    first = _compute(walk_id, started_at, points)
    second = _compute(walk_id, started_at, list(reversed(points)))

    assert first.facts == second.facts
    assert first.segments == second.segments


def test_duplicate_client_sequence_is_rejected(walk_id, started_at, point):
    points = [point(0, 0, seq=1), point(5, 7, seq=1)]

    with pytest.raises(ValueError, match="client_seq"):
        _compute(walk_id, started_at, points, 5)


def test_walk_stop_walk_boundaries(walk_id, started_at, point):
    result = _compute(walk_id, started_at, _walk_stop_walk(point))
    facts = result.facts

    assert facts.stop_count == 1
    assert facts.stop_s >= 25
    assert facts.moving_s + facts.stop_s <= facts.duration_s
    assert 150 <= facts.moving_distance_m <= facts.distance_m <= 180
    assert facts.avg_speed_mps is not None
    assert 1.3 <= facts.avg_speed_mps <= 1.5


def test_low_accuracy_is_rejected_but_unknown_is_kept(walk_id, started_at, point):
    points = [
        point(0, 0),
        point(5, 7, accuracy=80),
        point(10, 14, accuracy=None),
        point(15, 21),
    ]
    result = _compute(walk_id, started_at, points, 15)

    assert result.quality.rejected_low_accuracy == 1
    assert result.quality.unknown_accuracy == 1
    assert result.facts.fix_count == 3


def test_jump_and_gap_never_become_distance_or_stop(walk_id, started_at, point):
    jump = _compute(
        walk_id,
        started_at,
        [point(0, 0), point(5, 7), point(10, 500), point(15, 507)],
        15,
    )
    gap = _compute(walk_id, started_at, [point(0, 0), point(600, 0)], 600)

    assert jump.quality.jump_breaks == 1
    assert jump.facts.distance_m == 14
    assert gap.quality.gap_breaks == 1
    assert gap.facts.stop_count == 0
    assert len(gap.gaps) == 1


def test_pause_resume_chains_are_not_joined(walk_id, started_at, point):
    result = _compute(
        walk_id,
        started_at,
        [
            point(0, 0, chain_index=0),
            point(5, 7, chain_index=0),
            point(35, 87, chain_index=1),
            point(40, 94, chain_index=1),
        ],
        40,
    )

    assert result.quality.explicit_breaks == 1
    assert result.facts.distance_m == 14
    assert len(result.segments) == 2


def test_out_of_order_time_breaks_the_segment(walk_id, started_at, point):
    result = _compute(
        walk_id,
        started_at,
        [point(0, 0, seq=0), point(5, 7, seq=1), point(5, 7, seq=2)],
        10,
    )

    assert result.quality.rejected_out_of_order == 1
    assert result.facts.distance_m == 7


def test_points_outside_the_session_are_rejected(walk_id, started_at, point):
    result = _compute(
        walk_id,
        started_at,
        [
            point(-60, 0, seq=0),
            point(0, 0, seq=1),
            point(60, 10, seq=2),
            point(120, 20, seq=3),
        ],
        60,
    )

    assert result.quality.rejected_before_start == 1
    assert result.quality.rejected_after_end == 1
    assert result.facts.duration_s == 60


def test_mock_origin_and_stop_anchor_are_preserved(walk_id, started_at, point):
    mock = _compute(
        walk_id,
        started_at,
        [point(0, 0, is_mock=True), point(5, 7, is_mock=True)],
        5,
    )
    stopped = _compute(walk_id, started_at, _walk_stop_walk(point))

    assert mock.facts.evidence_origin == "mock"
    assert mock.quality.mock_fixes == 2
    assert stopped.events[0].started_at < stopped.events[0].ended_at
    assert stopped.events[0].route_offset_m > 70


def test_contract_rejects_naive_time_and_never_carries_pet_identity(point):
    payload = point(0, 0).model_dump()
    payload["at"] = datetime(2026, 9, 1, 9)  # noqa: DTZ001 - 거부할 naive 입력

    with pytest.raises(ValueError, match="timezone"):
        WalkEvidencePoint(**payload)
    assert "dog_id" not in WalkEvidencePoint.model_fields
    assert "pet_ids" not in WalkEvidencePoint.model_fields


def test_result_uses_server_walk_id(walk_id, started_at, point):
    result = _compute(walk_id, started_at, [point(0, 0)], 1)

    assert result.facts.walk_id == uuid.UUID(str(walk_id))
