"""Stored chunk/analysis -> verified candidates -> records-first stamps. No external services."""

import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from daengs_backend.config import settings
from daengs_backend.orchestration.contracts import PrincipalContext
from daengs_backend.schemas.walk import WalkFinalizeRequest, WalkPointUpload
from daengs_backend.services import walk_diary_input as reader
from daengs_backend.services.walk_analysis import build_analysis_models
from daengs_backend.services.walk_chunk import encode_chunk
from daengs_backend.services.walk_diary_observations import prepare_observation_source
from daengs_backend.services.walk_diary_prepare import prepare_saved_diary
from daengs_backend.services.walk_finalize import prepare_finalized_walk
from daengs_walk import analyze_walk, build_cellophane
from daengs_walk.diary_observations import MAX_OBSERVATIONS
from daengs_walk.diary_output import assemble_diary
from daengs_walk.diary_stamps import StampPolicy, prepare_stamps
from tests.walk.test_walk_photo_input import AT, OWNER, SESSION, WALK, entry
from tests.walk.test_walk_photo_input import row as photo_manifest


def uploaded(samples):
    """Samples are (seconds, north metres, optional chain, optional accuracy)."""
    return [
        WalkPointUpload(
            client_seq=i,
            at=AT + timedelta(seconds=s[0]),
            lat=round(37.5 + s[1] / 111195, 6),
            lng=127,
            chain_index=s[2] if len(s) > 2 else 0,
            accuracy_m=s[3] if len(s) > 3 else 5,
        )
        for i, s in enumerate(samples)
    ]


def stored(points):
    chunks = []
    for start in range(0, len(points), 10):
        part = points[start : start + 10]
        chunks.append(
            SimpleNamespace(
                seq_from=start,
                seq_to=start + len(part) - 1,
                point_count=len(part),
                payload=encode_chunk(part),
            )
        )
    walk = SimpleNamespace(
        id=WALK,
        app_user_id=OWNER,
        client_session_id=SESSION,
        started_at=AT,
        ended_at=max((p.at for p in points), default=AT),
        pet_ids=[],
        analysis_state="derived",
        points=chunks,
    )
    prepared = prepare_finalized_walk(
        chunks,
        WalkFinalizeRequest(
            expected_point_count=len(points),
            terminal_client_seq=len(points) - 1 if points else None,
        ),
    )
    evidence = analyze_walk(WALK, walk.started_at, walk.ended_at, prepared.points)
    analysis = build_analysis_models(prepared, evidence, build_cellophane(evidence))
    analysis.id = uuid.UUID(int=99)
    return walk, analysis, prepared.points


def varied_route():
    samples, seconds, metres = [(0, 0)], 0, 0
    for count, speed in [(6, 2), (3, 0), (6, 2), (3, 0.7), (6, 2), (3, 4), (6, 2)]:
        for _ in range(count):
            seconds += 10
            metres += speed * 10
            samples.append((seconds, metres))
    return stored(uploaded(samples))


def snapshot(walk, analysis, *, records=(), photos=None):
    return reader.assemble_input(
        walk,
        analysis,
        records,
        [],
        photos,
        [],
        observation_source=prepare_observation_source(walk, analysis),
    )


def test_saved_route_produces_all_motion_kinds_with_real_fix_anchors():
    walk, analysis, points = varied_route()
    result = snapshot(walk, analysis)
    pool = result.observation_source.pool
    assert {o.kind for o in pool.observations} == {
        "observed_dwell",
        "observed_fast",
        "observed_slow",
    }
    assert pool.baseline_mps == pytest.approx(2, abs=0.02)
    assert result.source.evidence_origin == "device"
    by_seq = {p.client_seq: p for p in points}
    for candidate in pool.observations:
        anchor = candidate.anchor
        fix = by_seq[anchor.source_fixes[0].client_seq]
        assert candidate.started_at <= fix.at <= candidate.ended_at
        assert (anchor.event_at, anchor.location_at, anchor.point.lat, anchor.point.lng) == (
            fix.at,
            fix.at,
            fix.lat,
            fix.lng,
        )
        assert candidate.analysis_id == str(analysis.id)
        assert (
            candidate.subject == "recording_device" and candidate.action_meaning == "not_inferred"
        )
    dwell = next(o for o in pool.observations if o.kind == "observed_dwell")
    assert (dwell.started_at, dwell.ended_at) == (
        AT + timedelta(seconds=60),
        AT + timedelta(seconds=90),
    )


def test_dwell_anchor_uses_a_real_fix_instead_of_the_average_position():
    walk, analysis, points = stored(uploaded([(0, 0), (10.123, 1), (20.123, 3)]))
    dwell = snapshot(walk, analysis).source.observations[0]
    average = analysis.motion_events[0]
    assert dwell.kind == "observed_dwell"
    assert dwell.anchor.point.lat != average["lat"]
    assert dwell.anchor.point.lat == points[1].lat
    assert dwell.anchor.location_at == points[1].at == AT + timedelta(seconds=10.123)
    assert dwell.anchor.source_fixes[0].client_seq == points[1].client_seq


@pytest.mark.parametrize("record_times", [[], [75], [75, 165, 255, 310]])
def test_records_none_short_or_sufficient_flow_into_existing_stamps(record_times):
    walk, analysis, _ = varied_route()
    records = []
    for index, second in enumerate(record_times):
        record = entry()
        record.id = uuid.UUID(int=index + 1)
        record.payload["recorded_at"] = (AT + timedelta(seconds=second)).isoformat()
        records.append(record)
    source = snapshot(walk, analysis, records=records).source
    prepared = prepare_stamps(source, StampPolicy(target_scene_count=3))
    bundle = assemble_diary(source, prepared.plan, None)
    assert len(bundle.scenes) == max(3, len(records))
    assert sum(s.user_record is not None for s in bundle.scenes) == len(records)
    assert {s.core.identity for s in bundle.scenes if s.user_record} == {
        f"walk_entry:{r.id}" for r in records
    }
    if record_times == [75]:
        assert not any(
            s.observation and s.observation.kind == "observed_dwell" for s in bundle.scenes
        )


def test_photo_is_also_preserved_as_primary_material():
    walk, analysis, _ = varied_route()
    source = snapshot(walk, analysis, photos=photo_manifest()).source
    result = prepare_stamps(source, StampPolicy(target_scene_count=4))
    assert result.counts["user_records"] == 1 and result.counts["supplemented"] == 3
    assert assemble_diary(source, result.plan, None).scenes[-1].user_record.kind == "photo"


@pytest.mark.parametrize(
    "samples",
    [
        [],
        [(0, 0)],
        [(0, 0), (2, 0)],
        [(0, 0), (70, 0)],
        [(0, 0), (10, 5000), (20, 10000)],
        [(0, 0, 0, 5), (10, 0, 0, 80), (20, 0, 0, 5)],
        [(0, 0), (10, 100)],
    ],
)
def test_gaps_noise_short_or_insufficient_samples_do_not_become_actions(samples):
    walk, analysis, _ = stored(uploaded(samples))
    source = snapshot(walk, analysis).source
    assert source.route.status == "ready" and source.observations == ()
    result = prepare_stamps(source, StampPolicy(target_scene_count=3))
    assert result.counts["remaining_deficit"] == 3


def test_pause_and_missing_interval_split_dwell_support():
    for samples in [
        [(0, 0, 0), (10, 0, 0), (20, 0, 1), (30, 0, 1)],
        [(0, 0), (10, 0), (100, 0), (110, 0)],
    ]:
        walk, analysis, _ = stored(uploaded(samples))
        observations = snapshot(walk, analysis).source.observations
        assert len(observations) == 2
        assert all(o.ended_at - o.started_at == timedelta(seconds=10) for o in observations)


@pytest.mark.parametrize(
    "change,reason",
    [
        ("pending", "analysis_not_finalized"),
        ("missing", "analysis_not_finalized"),
        ("version", "unsupported_analysis_version"),
        ("session", "analysis_session_mismatch"),
        ("fingerprint", "invalid_analysis_source"),
        ("chunk", "invalid_analysis_source"),
        ("summary", "invalid_analysis_source"),
        ("event", "analysis_replay_mismatch"),
    ],
)
def test_unavailable_motion_retains_user_records(change, reason):
    walk, analysis, _ = varied_route()
    if change == "pending":
        walk.analysis_state = "pending"
    elif change == "missing":
        analysis = None
    elif change == "version":
        analysis.calculation_version = 999
    elif change == "session":
        walk.id = uuid.UUID(int=888)
    elif change == "fingerprint":
        analysis.input_fingerprint = "sha256:" + "b" * 64
    elif change == "chunk":
        walk.points.pop()
    elif change == "summary":
        analysis.stop_count += 1
    else:
        analysis.motion_events[0]["duration_s"] += 1
    source = snapshot(walk, analysis, records=[entry()]).source
    assert source.route.status == "unavailable" and source.route.reason == reason
    assert source.observations == ()
    assert len(prepare_stamps(source, StampPolicy(target_scene_count=3)).plan.scenes) == 1


def test_revision_tracks_raw_analysis_and_policy_while_chunk_order_does_not():
    walk, analysis, _ = varied_route()
    first = snapshot(walk, analysis).source
    walk.points.reverse()
    assert snapshot(walk, analysis).source == first
    analysis.id = uuid.UUID(int=100)
    changed = snapshot(walk, analysis).source
    assert changed.revision() != first.revision()
    assert {o.version for o in changed.observations}.isdisjoint(
        o.version for o in first.observations
    )


def test_capacity_is_explicit_and_preserves_dwell_priority():
    samples, t, m = [(0, 0)], 0, 0
    for _ in range(MAX_OBSERVATIONS + 1):
        t += 10
        samples.append((t, m))
        t += 10
        m += 20
        samples.append((t, m))
    walk, analysis, _ = stored(uploaded(samples))
    pool = prepare_observation_source(walk, analysis).pool
    assert len(pool.observations) == MAX_OBSERVATIONS
    assert pool.total_candidates == MAX_OBSERVATIONS + 1 and pool.omitted_at_capacity == 1


def test_mock_origin_is_reported_without_calling_it_device_data():
    points = uploaded([(0, 0), (10, 0), (20, 0)])
    points[0] = points[0].model_copy(update={"is_mock": True})
    walk, analysis, _ = stored(points)
    assert snapshot(walk, analysis).source.evidence_origin == "mixed"


async def test_owned_storage_reader_supplies_candidates_to_internal_preparation(monkeypatch):
    walk, analysis, _ = varied_route()
    session = SimpleNamespace(new=set(), dirty=set(), deleted=set(), expire_all=Mock())
    locked = AsyncMock(return_value=walk)
    monkeypatch.setattr(reader.walks, "get_owned_for_update", locked)
    monkeypatch.setattr(reader.storyboards, "latest_analysis", AsyncMock(return_value=analysis))
    monkeypatch.setattr(reader.entries, "entries", AsyncMock(return_value=[]))
    for flag in (
        "walk_entry_v2_enabled",
        "walk_entry_context_enabled",
        "walk_photo_metadata_enabled",
    ):
        monkeypatch.setattr(settings, flag, False)
    result = await prepare_saved_diary(
        session,
        PrincipalContext(kind="APP_USER", subject=str(OWNER)),
        WALK,
        StampPolicy(target_scene_count=3),
    )
    locked.assert_awaited_once_with(session, OWNER, WALK)
    assert result.prepared.counts["supplemented"] == 3
    assert result.input.observation_source.pool.total_candidates >= 3
