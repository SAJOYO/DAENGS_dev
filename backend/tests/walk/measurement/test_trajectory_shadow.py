import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from daengs_backend.schemas.walk import WalkPointUpload
from daengs_backend.schemas.walk_motion import MotionManifest, MotionObservation
from daengs_backend.services import walk_motion
from daengs_backend.services.walk_finalize import walk_input_fingerprint
from daengs_backend.services.walk_motion_contract import MotionConflict
from daengs_backend.services.walk_motion_engine import replay
from daengs_backend.services.walk_trajectory_shadow import (
    ShadowAssembler,
    calculate_shadow,
    replay_shadow,
)
from daengs_walk.trajectory import EvidenceJournal, JournalEvent, SourceRef
from tools.check_walk_trajectory_shadow import calculate_export, main, summary

ROOT = Path(__file__).parents[1] / "fixtures"
CASES = json.loads((ROOT / "gps-motion-replay-v1.json").read_text())["cases"]
PRECISE = json.loads((ROOT / "gps-motion-precision-v1.json").read_text())["cases"]


def case(name):
    return copy.deepcopy(next(c for c in CASES if c["name"] == name))


def inputs(payload):
    return (
        MotionManifest.model_validate(payload["manifest"]),
        [MotionObservation.model_validate(p) for p in payload["points"]],
        [WalkPointUpload.model_validate(p) for p in payload["raw_points"]],
    )


@pytest.mark.parametrize("payload", CASES, ids=lambda p: p["name"])
def test_existing_kotlin_golden_distance_and_paths_keep_their_source_owners(payload):
    result = calculate_export(payload)
    expected = payload["expected"]
    assert result.motion_distance_m == pytest.approx(expected["distance_m"], abs=1e-7, rel=1e-10)
    assert result.motion_active_duration_ns == expected["recording_duration_nanos"]
    assert result.motion_segments == tuple(tuple(p) for p in expected["segments"])
    assert sum(s.walking_distance_m for s in result.walking_sections) == pytest.approx(
        expected["distance_m"], abs=1e-7, rel=1e-10
    )
    projected = [tuple(p.ingress_seq for p in s.point_refs) for s in result.walking_sections]
    assert projected == [tuple(p) for p in expected["segments"] if len(p) > 1]
    assert [p.ref.ingress_seq for p in result.snapshot.ledger.points] == list(
        range(len(payload["points"]))
    )


@pytest.mark.parametrize("payload", PRECISE, ids=lambda p: p["name"])
def test_original_precision_backup_has_distinct_identity_and_matches_kotlin(payload):
    result = calculate_export(payload)
    assert result.snapshot.key.coordinate_basis == "device-fix-bits-v1"
    assert result.snapshot.key.precision_fingerprint == payload["precision_fingerprint"][7:]
    assert result.motion_distance_m == pytest.approx(
        payload["expected"]["distance_m"], abs=1e-7, rel=1e-10
    )


@pytest.mark.parametrize(
    "name",
    [
        "empty",
        "slow-accumulation",
        "high-speed-reentry",
        "poor-position",
        "out-of-order",
        "pause",
        "stop-tie",
    ],
)
@pytest.mark.parametrize("batch_size", [2, 16, 256])
def test_step_by_step_and_batched_assembly_seal_to_identical_results(name, batch_size):
    payload = case(name)
    assert calculate_export(payload) == calculate_export(payload, step_batch_size=batch_size)


def test_observed_high_speed_path_is_independent_of_two_walking_sections():
    result = calculate_export(case("high-speed-reentry"))
    assert len(result.observed_runs) == 1
    assert len(result.walking_sections) == 2
    assert any(
        i.continuity == "connected" and i.walking_use == "excluded" and "HIGH_SPEED" in i.reasons
        for i in result.snapshot.ledger.intervals
    )


def test_real_gap_and_pause_split_observed_paths_too():
    for name in ("observation-gap", "pause"):
        result = calculate_export(case(name))
        assert len(result.observed_runs) == 2
        assert len(result.walking_sections) == 2
    paused = calculate_export(case("pause"))
    metrics = paused.snapshot.ledger.metrics()
    assert metrics.paused_duration_ns > 0
    assert (
        metrics.known_duration_ns == paused.motion_active_duration_ns + metrics.paused_duration_ns
    )


def test_ending_after_walking_keeps_last_observation_and_record_end_distinct():
    manifest, observations, raw = inputs(case("high-speed-reentry"))
    raw, observations = raw[:3], observations[:3]
    manifest.point_count = manifest.epochs[0].persisted_count = 3
    manifest.epochs[0].target_ingress_seq = 2
    manifest.raw_input_fingerprint = walk_input_fingerprint(raw)
    result = replay_shadow(manifest, observations, raw, owner_id="test")
    bounds = result.boundaries
    assert bounds.last_walking != bounds.record_end
    assert bounds.record_end.control_kind == "epoch_end"
    assert bounds.record_end.ingress_seq is None
    assert bounds.last_observed.ingress_seq == 2
    assert bounds.last_walking.ingress_seq == 1


def test_out_of_order_time_is_kept_as_evidence_and_never_sorted_or_clamped():
    payload = case("out-of-order")
    result = calculate_export(payload)
    events = [e for e in result.snapshot.ledger.journal.events if e.kind == "observation"]
    assert [e.ref.ingress_seq for e in events] == list(range(len(payload["points"])))
    bad = [e for e in events if "OUT_OF_ORDER" in e.time_reasons]
    assert bad
    for event in bad:
        assert event.elapsed_ns is None
        assert (
            event.original_elapsed_ns
            == payload["points"][event.ref.ingress_seq]["elapsed_realtime_nanos"]
        )


def test_empty_epoch_controls_do_not_borrow_or_invent_observation_sequences():
    result = calculate_export(case("empty"))
    events = result.snapshot.ledger.journal.events
    assert len(events) == 2
    assert events[0].ref != events[1].ref
    assert all(e.ref.ingress_seq is None for e in events)
    assert result.boundaries.first_observed is None
    assert not result.locations


@pytest.mark.parametrize(
    "change", ["raw", "metadata", "precision", "partial_precision", "precision_owner"]
)
def test_detached_input_integrity_is_checked_before_returning_a_result(change):
    payload = copy.deepcopy(next(c for c in PRECISE if c["name"] == "walking"))
    if change == "raw":
        payload["raw_points"][1]["lng"] += 1
    elif change == "metadata":
        payload["evidence_fingerprint"] = "sha256:" + "0" * 64
    elif change == "precision":
        payload["precision_fingerprint"] = "sha256:" + "0" * 64
    elif change == "partial_precision":
        del payload["precision_points"]
    else:
        payload["precision_manifest"]["client_session_id"] = "22222222-2222-2222-2222-222222222222"
    with pytest.raises(ValueError):
        calculate_export(payload)


def test_invalid_or_incomplete_replay_cannot_be_sealed_as_a_valid_shadow():
    payload = case("walking")
    manifest, observations, raw = inputs(payload)
    assembler = ShadowAssembler(manifest, observations, raw, owner_id="test")
    reference = replay(manifest, observations, raw)
    with pytest.raises(MotionConflict, match="steps_incomplete"):
        assembler.seal(reference)
    raw.reverse()
    with pytest.raises(ValueError):
        replay_shadow(manifest, observations, raw, owner_id="test")


async def test_service_reuses_owner_checked_detached_input_and_does_not_write(monkeypatch):
    payload = case("walking")
    manifest, observations, raw = inputs(payload)
    session = SimpleNamespace(commit=AsyncMock(side_effect=AssertionError("must not write")))
    detached = AsyncMock(
        return_value=(manifest, raw, observations, payload["evidence_fingerprint"], None)
    )
    monkeypatch.setattr(walk_motion, "completed_input", detached)
    result = await calculate_shadow(session, "owner", "walk-id")
    detached.assert_awaited_once_with(session, "owner", "walk-id")
    session.commit.assert_not_awaited()
    assert result.snapshot.scope.owner_id == "owner"


async def test_owner_or_completion_failure_is_not_replaced_with_legacy_fallback(monkeypatch):
    detached = AsyncMock(side_effect=MotionConflict("motion_backup_incomplete"))
    monkeypatch.setattr(walk_motion, "completed_input", detached)
    with pytest.raises(MotionConflict, match="motion_backup_incomplete"):
        await calculate_shadow(object(), "owner", "walk-id")


def test_cli_default_report_contains_no_coordinates_or_event_timestamps(tmp_path, capsys):
    source = tmp_path / "input.json"
    source.write_text(json.dumps(case("walking")))
    main([str(source)])
    report = json.loads(capsys.readouterr().out)
    assert report["reports"][0]["walking_section_count"] == 1
    text = json.dumps(report)
    assert '"lat"' not in text and '"lng"' not in text and '"original_elapsed_ns"' not in text
    assert summary(calculate_export(case("walking")))["last_walking_seq"] == 11


def test_duplicate_epoch_control_and_false_control_kind_are_rejected():
    ref = SourceRef(
        session_id="s", source_epoch="e", clock_epoch_id="c", control_kind="epoch_start"
    )
    with pytest.raises(ValueError, match="event kind"):
        JournalEvent(ref=ref, kind="end", elapsed_ns=1)
    with pytest.raises(ValueError, match="unique"):
        EvidenceJournal(
            events=(
                JournalEvent(ref=ref, kind="start", elapsed_ns=0),
                JournalEvent(ref=ref, kind="start", elapsed_ns=1),
                JournalEvent(
                    ref=SourceRef(
                        session_id="s",
                        source_epoch="e",
                        clock_epoch_id="c",
                        control_kind="epoch_end",
                    ),
                    kind="end",
                    elapsed_ns=2,
                ),
            )
        )


def test_far_valid_reentry_keeps_both_walking_sections_and_a_real_observed_break():
    payload = case("high-speed-reentry")
    for point in payload["raw_points"][4:]:
        point["lng"] += 0.01
    manifest, observations, raw = inputs(payload)
    manifest.raw_input_fingerprint = walk_input_fingerprint(raw)
    result = replay_shadow(manifest, observations, raw, owner_id="test")
    assert len(result.walking_sections) == len(result.observed_runs) == 2
    assert result.walking_sections[1].point_refs[0].ingress_seq == 4
    assert any("OBSERVED_EDGE_JUMP" in i.reasons for i in result.snapshot.ledger.intervals)


def test_decoded_coordinate_bits_are_part_of_identity_even_with_same_coarse_upload_hash():
    from decimal import Decimal

    manifest, observations, raw = inputs(case("walking"))
    original = replay_shadow(manifest, observations, raw, owner_id="test")
    shifted = [p.model_copy(update={"lng": p.lng + Decimal("0.00000001")}) for p in raw]
    assert walk_input_fingerprint(shifted) == manifest.raw_input_fingerprint
    changed = replay_shadow(manifest, observations, shifted, owner_id="test")
    assert changed.snapshot.key.input_fingerprint != original.snapshot.key.input_fingerprint
    assert changed.snapshot.measurement_id != original.snapshot.measurement_id


def test_cli_never_overwrites_the_input_export(tmp_path, capsys):
    source = tmp_path / "input.json"
    source.write_text(json.dumps(case("walking")))
    before = source.read_bytes()
    with pytest.raises(SystemExit) as error:
        main([str(source), "--output", str(source)])
    assert error.value.code == 2
    assert source.read_bytes() == before
    assert "failed" in capsys.readouterr().err


def test_interval_ownership_updates_during_replay_before_sealing():
    manifest, observations, raw = inputs(case("slow-accumulation"))
    assembler = ShadowAssembler(manifest, observations, raw, owner_id="test")
    intermediate_totals = []

    def consume(step):
        assembler.accept(step)
        total = sum(i.walking_distance_m for i in assembler.intervals)
        intermediate_totals.append(total)
        if step["decision"]["client_seq"] < manifest.point_count - 1:
            with pytest.raises(MotionConflict, match="steps_incomplete"):
                assembler.seal({})

    expected = replay(manifest, observations, raw, consume)
    assert 0 < intermediate_totals[-2] <= expected["distance_m"]
    result = assembler.seal(expected)
    assert result.snapshot.ledger.metrics().walking_distance_m == pytest.approx(
        expected["distance_m"]
    )
    assert assembler.seal(expected) == result


def test_multi_chunk_journal_is_stable_under_actual_incremental_assembly():
    from datetime import timedelta
    from decimal import Decimal

    manifest, observations, raw = inputs(case("walking"))
    first_point, first_observation = raw[0], observations[0]
    count = 1500
    raw = [
        first_point.model_copy(
            update={
                "client_seq": i,
                "at": first_point.at + timedelta(seconds=i * 3),
                "lng": Decimal("0.000036") * i,
            }
        )
        for i in range(count)
    ]
    observations = [
        first_observation.model_copy(
            update={
                "client_seq": i,
                "elapsed_realtime_nanos": (1 + i * 3) * 10**9,
                "received_elapsed_nanos": (1 + i * 3) * 10**9,
            }
        )
        for i in range(count)
    ]
    manifest.point_count = count
    manifest.epochs[0].persisted_count = count
    manifest.epochs[0].target_ingress_seq = count - 1
    manifest.epochs[0].ended_elapsed_nanos = count * 3 * 10**9
    manifest.raw_input_fingerprint = walk_input_fingerprint(raw)
    a = replay_shadow(manifest, observations, raw, owner_id="test")
    b = replay_shadow(manifest, observations, raw, owner_id="test", step_batch_size=256)
    assert a.snapshot.ref() == b.snapshot.ref()
    assert a.walking_sections == b.walking_sections
    assert len(a.walking_sections[0].point_refs) == count
