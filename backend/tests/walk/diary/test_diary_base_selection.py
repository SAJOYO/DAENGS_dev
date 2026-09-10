"""Real source support, records-first coverage and boundary semantics; no providers/DB."""

from dataclasses import replace
from datetime import timedelta

import pytest

from daengs_backend.services.walk_diary_base_board import assemble_saved_base_board
from daengs_backend.services.walk_diary_input import assemble_input
from daengs_backend.services.walk_diary_observations import prepare_observation_source
from daengs_walk.diary_board import PreparedBaseBoard, RecordCore, VerifiedBoardRoute
from daengs_walk.diary_board_assembly import assemble_base_board
from daengs_walk.diary_board_selection import prepare_base_board
from daengs_walk.diary_input import DiaryInput, material_ref
from daengs_walk.diary_stamps import prepare_stamps
from tests.walk.support.base_board import policy, saved_case
from tests.walk.support.diary import record, source
from tests.walk.support.observations import varied_route
from tests.walk.support.photo_input import AT, entry


def checkpoints(plan):
    return [s.core for s in plan.stamps if s.core.kind == "route_checkpoint"]


def test_ordinary_walk_gains_observed_checkpoints_and_extra_boundaries():
    assembled, route, points = saved_case()
    plan = prepare_base_board(assembled.source, policy(), route=route)
    assert assembled.source.observations == ()
    assert plan.counts["checkpoints"] == 5 and plan.counts["boundaries"] == 2
    assert plan.counts["intermediate_total"] == 5 and plan.counts["total"] == 7
    assert plan.counts["remaining_deficit"] == 0
    assert [s.core.boundary for s in (plan.stamps[0], plan.stamps[-1])] == ["start", "end"]
    by_seq = {p.client_seq: p for p in points}
    for core in checkpoints(plan):
        fix = by_seq[core.anchor.source_fixes[0].client_seq]
        assert (core.anchor.point.lat, core.anchor.point.lng) == (fix.lat, fix.lng)
        assert core.anchor.event_at == core.anchor.location_at == fix.at
        assert core.anchor.method == "observed" and core.route == assembled.source.route
    # Target is a desired intermediate count, not the old lookup selector's eight-anchor budget.
    bigger = prepare_base_board(assembled.source, policy(10), route=route)
    assert bigger.counts["total"] == 12


def test_uncovered_longest_interval_is_split_using_an_actual_near_midpoint_fix():
    assembled, route, _ = saved_case()
    first = checkpoints(prepare_base_board(assembled.source, policy(1), route=route))[0]
    assert first.route_m == pytest.approx(1000, abs=1)
    second = checkpoints(prepare_base_board(assembled.source, policy(2), route=route))
    assert first in second
    # Sub-metre coordinate quantization can make either half the longer interval.
    other = next(c for c in second if c != first)
    assert min(abs(other.route_m - 500), abs(other.route_m - 1500)) < 1


def test_user_record_covers_its_own_route_visit_without_moving_or_merging_it():
    assembled, route, points = saved_case()
    raw = record().model_dump(mode="json")
    fix = points[50]
    raw["anchor"].update(
        event_at=fix.at,
        location_at=fix.at,
        method="observed",
        point={"lat": fix.lat, "lng": fix.lng},
    )
    value = assembled.source.model_dump(mode="json")
    value["records"] = [raw]
    snapshot = DiaryInput.model_validate(value)
    original = snapshot.records[0]
    plan = prepare_base_board(snapshot, policy(3), route=route)
    assert plan.counts["user_records"] == 1 and plan.counts["checkpoints"] == 2
    selected = next(s for s in plan.stamps if isinstance(s.core, RecordCore))
    assert selected.core.record == original and selected.core_ref == material_ref(original)
    assert all(abs(c.route_m - 1000) >= 100 for c in checkpoints(plan))
    assert selected.id == prepare_stamps(snapshot, policy(3).intermediate).plan.scenes[0].id


def test_more_user_records_than_target_all_survive_including_unlocated_and_photo():
    records = (
        record("one", "05", unlocated=True),
        record("two", "05", photo=True),
        record("three", "06"),
    )
    snapshot = source(*records)
    plan = prepare_base_board(snapshot, policy(1))
    assert plan.counts["total"] == 5 and plan.counts["checkpoints"] == 0
    actual = [s.core.record for s in plan.stamps if isinstance(s.core, RecordCore)]
    assert {r.ref.identity: r for r in actual} == {r.ref.identity: r for r in records}
    assert next(r for r in actual if r.ref.id == "one").anchor.point is None


def test_existing_observation_priority_and_backgrounds_remain_the_same():
    walk, analysis, _ = varied_route()
    motion = prepare_observation_source(walk, analysis)
    assembled = assemble_input(walk, analysis, [], [], None, [], observation_source=motion)
    result = assemble_saved_base_board(assembled, policy())
    old = prepare_stamps(assembled.source, policy().intermediate)
    selected = [s for s in result.plan.stamps if s.core.kind == "movement_observation"]
    assert [s.core_ref for s in selected] == [s.core for s in old.plan.scenes]
    assert result.plan.counts["supplemented"] == 3
    assert all(s.core.observation.action_meaning == "not_inferred" for s in selected)


@pytest.mark.parametrize(
    "samples", [[], [(0, 0)], [(0, 0), (2, 1)], [(0, 0), (90, 1000)], [(0, 0), (10, 5000)]]
)
def test_noisy_short_or_missing_route_never_manufactures_checkpoint_actions(samples):
    assembled, route, _ = saved_case(samples)
    plan = prepare_base_board(assembled.source, policy(), route=route)
    assert checkpoints(plan) == [] and plan.counts["total"] == 2
    assert all(s.core.kind == "session_boundary" for s in plan.stamps)
    assert plan.counts["remaining_deficit"] == 5
    assert all(s.body for s in assemble_base_board(assembled.source, plan, route=route).scenes)


@pytest.mark.parametrize("pause", [False, True])
def test_gps_gap_and_pause_keep_separate_coverage_blocks(pause):
    samples = [(i * 10, i * 20, 0) for i in range(21)]
    offset = 210 if pause else 600
    samples += [(offset + i * 10, 1000 + i * 20, 1 if pause else 0) for i in range(21)]
    assembled, route, points = saved_case(samples)
    plan = prepare_base_board(assembled.source, policy(6), route=route)
    selected = checkpoints(plan)
    assert {c.block for c in selected} == {0, 1}
    assert all(c.route_m <= 801 for c in selected)  # No distance added over the 600m break.
    assert all(c.anchor.event_at in {p.at for p in points} for c in selected)
    assert all(len(c.anchor.source_fixes) == 1 for c in selected)


def test_boundary_uses_exact_session_time_and_does_not_borrow_a_late_fix():
    assembled, route, _ = saved_case([(10, 0), (20, 20), (30, 40)])
    plan = prepare_base_board(assembled.source, policy(), route=route)
    start, end = plan.stamps[0].core, plan.stamps[-1].core
    assert start.anchor.event_at == AT and start.anchor.point is None
    assert end.anchor.event_at == AT + timedelta(seconds=30) and end.anchor.point is not None
    later = AT + timedelta(seconds=40)
    snapshot = assembled.source.model_copy(update={"ended_at": later})
    evidence = replace(
        route.evidence, facts=route.evidence.facts.model_copy(update={"ended_at": later})
    )
    plan = prepare_base_board(snapshot, policy(), route=VerifiedBoardRoute(route.version, evidence))
    assert plan.stamps[-1].core.anchor.event_at == later
    assert plan.stamps[-1].core.anchor.point is None


@pytest.mark.parametrize("change", ["fingerprint", "walk", "time", "origin"])
def test_foreign_or_stale_route_is_rejected(change):
    assembled, route, _ = saved_case()
    snapshot = assembled.source
    if change == "fingerprint":
        snapshot = snapshot.model_copy(
            update={"route": snapshot.route.model_copy(update={"input_fingerprint": "b" * 64})}
        )
    elif change == "walk":
        snapshot = snapshot.model_copy(update={"walk_id": "another-walk"})
    elif change == "time":
        snapshot = snapshot.model_copy(
            update={"ended_at": snapshot.ended_at + timedelta(seconds=1)}
        )
    else:
        snapshot = snapshot.model_copy(update={"evidence_origin": "mock"})
    with pytest.raises(ValueError, match="another source/version"):
        prepare_base_board(snapshot, policy(), route=route)


def test_invalid_saved_analysis_keeps_originals_and_boundaries_without_route():
    walk, analysis, _ = varied_route()
    analysis.input_fingerprint = "sha256:" + "b" * 64
    observation = prepare_observation_source(walk, analysis)
    assert observation.evidence is None
    assembled = assemble_input(
        walk, analysis, [entry()], [], None, [], observation_source=observation
    )
    result = assemble_saved_base_board(assembled, policy())
    assert result.plan.counts["total"] == 3
    assert result.board.scenes[1].body == entry().payload["note"]


def test_contract_roundtrip_and_replay_reject_forged_checkpoint_or_background():
    assembled, route, _ = saved_case()
    plan = prepare_base_board(assembled.source, policy(), route=route)
    assert PreparedBaseBoard.model_validate_json(plan.model_dump_json()) == plan
    changed = plan.model_dump(mode="json")
    changed["stamps"][1]["core"]["anchor"]["point"]["lat"] += 0.01
    forged = PreparedBaseBoard.model_validate(changed)
    with pytest.raises(ValueError, match="differs"):
        assemble_base_board(assembled.source, forged, route=route)
