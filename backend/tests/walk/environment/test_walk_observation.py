from datetime import timedelta

from daengs_walk.facts import compute_walk_facts
from daengs_walk.observation import (
    CANDIDATE_SPEED_MPS,
    extract_micro_observations,
    moving_speed_profile,
)


def _compute(walk_id, started_at, points, ended_s):
    return compute_walk_facts(
        walk_id,
        started_at,
        started_at + timedelta(seconds=ended_s),
        points,
    )


def test_chronic_slow_zone_survives_without_becoming_a_stop(walk_id, started_at, point):
    points = [point(t, t / 5 * 4) for t in range(0, 31, 5)]  # 0.8m/s
    computed = _compute(walk_id, started_at, points, 30)
    observations = extract_micro_observations(walk_id, computed.segments, computed.gaps)

    assert computed.facts.stop_count == 0
    assert len(observations) == 1
    assert observations[0].kind == "slow"
    assert observations[0].duration_s == 30
    assert observations[0].path_m > 20


def test_gap_is_absence_not_dwell(walk_id, started_at, point):
    computed = _compute(walk_id, started_at, [point(0, 0), point(600, 2)], 600)
    observation = extract_micro_observations(walk_id, computed.segments, computed.gaps)[0]

    assert observation.kind == "gap"
    assert observation.duration_s == 600
    assert observation.path_m == 0
    assert observation.span_m == 0
    assert observation.net_m > 0
    assert observation.abuts_break is True


def test_slow_windows_do_not_cross_a_chain_break(walk_id, started_at, point):
    computed = _compute(
        walk_id,
        started_at,
        [
            point(0, 0, chain_index=0),
            point(5, 4, chain_index=0),
            point(10, 8, chain_index=1),
            point(15, 12, chain_index=1),
        ],
        15,
    )
    observations = extract_micro_observations(walk_id, computed.segments, computed.gaps)

    assert len(observations) == 2
    assert observations[0].chain_index != observations[1].chain_index
    assert all(observation.abuts_break for observation in observations)


def test_short_wobble_is_not_an_observation(walk_id, started_at, point):
    computed = _compute(
        walk_id,
        started_at,
        [point(0, 0), point(2, 1), point(4, 5)],
        4,
    )

    assert extract_micro_observations(walk_id, computed.segments, computed.gaps) == ()


def test_path_net_and_span_remain_different_facts(walk_id, started_at, point):
    computed = _compute(
        walk_id,
        started_at,
        [point(0, 0), point(5, 4), point(10, 0), point(15, 4)],
        15,
    )
    observation = extract_micro_observations(walk_id, computed.segments, computed.gaps)[0]

    assert observation.path_m > observation.net_m
    assert observation.span_m > 0


def test_speed_profile_is_honestly_absent_until_five_samples(walk_id, started_at, point):
    short = _compute(
        walk_id,
        started_at,
        [point(t, t / 5 * 7) for t in range(0, 25, 5)],
        20,
    )
    enough = _compute(
        walk_id,
        started_at,
        [point(t, t / 5 * 7) for t in range(0, 31, 5)],
        30,
    )

    assert moving_speed_profile(short.segments) is None
    profile = moving_speed_profile(enough.segments)
    assert profile is not None
    assert profile.sample_n == 6
    assert profile.p50 <= profile.p70 <= profile.p80 <= profile.p90


def test_candidate_threshold_is_the_declared_exploration_range():
    assert CANDIDATE_SPEED_MPS == 1.0
