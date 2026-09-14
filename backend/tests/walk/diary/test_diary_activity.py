"""Composed movement through actual orchestration and persisted card contracts; no providers."""

import json
import uuid
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from daengs_backend.services.walk_diary import runtime as writing
from daengs_backend.services.walk_diary.model_input import normalize
from daengs_backend.services.walk_diary.preparation.board import (
    assemble_saved_base_board,
    with_scene_backgrounds,
)
from daengs_backend.services.walk_diary.preparation.diary import PreparedWalkDiary
from daengs_backend.services.walk_diary.preparation.input import InputAssembly
from daengs_backend.services.walk_diary.preparation.observations import ObservationSource
from daengs_backend.services.walk_diary.storage.board import load_board, store_board
from daengs_backend.services.walk_diary.writing import jobs as activity_jobs
from daengs_walk.diary.board.activity import activity_projection, movement_uses
from daengs_walk.diary.board.models import VerifiedBoardRoute
from daengs_walk.diary.contracts.input import DiaryInput, digest
from daengs_walk.diary.contracts.slots import SlotPolicy
from daengs_walk.diary.route.movement import pace_claims, phases_for, prepare_movement
from daengs_walk.diary.route.movement_policy import MovementPolicy
from daengs_walk.diary.route.observations import build_observation_pool
from daengs_walk.diary.selection.board import observed_anchor
from daengs_walk.evidence import analyze_walk
from tests.walk.diary.test_diary_route_patterns import input_case
from tests.walk.support.base_board import policy as board_policy
from tests.walk.support.route_patterns import route


def prepared(*, pin=True, note=False, gap=False, waypoints=None):
    source, _, _ = input_case("out_back", pin=pin)
    sample = route(
        "pace-return", waypoints or [(0, 0, 0), (120, 0, 120), (220, 0, 20), (240, 0, 16)]
    )
    if gap:
        sample = sample.model_copy(
            update={
                "points": tuple(
                    p
                    for p in sample.points
                    if not 150 < (p.at - sample.started_at).total_seconds() < 210
                )
            }
        )
    computed = analyze_walk(
        uuid.UUID(source.walk_id), sample.started_at, sample.ended_at, sample.points
    )
    version = source.route.model_copy(update={"input_fingerprint": digest(sample)})
    raw = source.model_dump(mode="json")
    raw.update(
        started_at=sample.started_at,
        ended_at=sample.ended_at,
        route=version,
        observations=build_observation_pool(computed, version).observations,
    )
    if pin:
        raw["records"][0]["anchor"] = observed_anchor(sample.points[-2]).model_copy(
            update={"time_basis": "recorded_at"}
        )
        if note:
            raw["records"][0]["content"] = {"kind": "note", "text": "물을 마시고 돌아가기로 했다."}
    source = DiaryInput.model_validate(raw)
    assembled = InputAssembly(source, (), ObservationSource(version, evidence=computed))
    base = assemble_saved_base_board(
        assembled, board_policy(3), slot_policy=SlotPolicy(movement={})
    )
    return base, VerifiedBoardRoute(version, computed)


async def provider(stage, payload, schema):
    if stage == "title":
        return {"titles": [{"id": c["id"], "text": "돌아오는 길의 기록"} for c in payload["cards"]]}
    if stage == "action":
        if "movement" in payload:
            materials = payload["movement"]["materials"]
            refs = [f["id"] for p in materials for f in [p, *p.get("changes", [])] if "id" in f]
            if payload.get("recorded_action"):
                refs.append(payload["recorded_action"]["id"])
            return {"text": "이 기록 무렵의 동선을 남겼다.", "evidence_ids": refs}
        return {"text": "냄새 맡기 행동을 기록했다."}
    return {"text": "", "evidence_ids": []}


def test_pace_thresholds_are_strict_and_duration_is_continuous_before_clipping():
    def nodes(speed, duration=10, block=0):
        return [
            {
                "block": block,
                "start_s": 0,
                "elapsed_s": duration,
                "speed": speed,
                "duration_s": duration,
            }
        ]

    policy = MovementPolicy()
    assert not pace_claims(nodes(0.5), 1, policy, "source")
    assert not pace_claims(nodes(1.5), 1, policy, "source")
    assert pace_claims(nodes(0.49), 1, policy, "source")[0]["meaning"] == "relative_slow"
    assert pace_claims(nodes(1.51), 1, policy, "source")[0]["meaning"] == "relative_fast"
    assert not pace_claims(nodes(0.4, 9), 1, policy, "source")
    claim = pace_claims(nodes(0.4, 12), 1, policy, "source")
    assert phases_for(claim, 0, 6) and phases_for(claim, 6, 12)
    split = nodes(0.4, 6) + [{**nodes(0.4, 6, 1)[0], "start_s": 10, "elapsed_s": 16}]
    assert not pace_claims(split, 1, policy, "source")


def test_return_and_last_slow_phase_share_one_slot_without_extending_slow_support():
    base, verified = prepared()
    catalog = prepare_movement(base.input.source, verified, MovementPolicy())
    slow = [c for c in catalog.claims if c["meaning"] == "relative_slow"]
    assert slow and slow[-1]["start_s"] == 220 and slow[-1]["end_s"] == 240
    assert any(c["meaning"] == "retrace" for c in catalog.claims)
    stamp = next(
        s
        for s in base.slots.stamps
        if s.scene_id == next(c.id for c in base.board.scenes if c.core.kind == "user_record")
    )
    movement = [e for e in stamp.evidence if e.part == "motion"]
    assert len(movement) == 1
    facts = movement[0].facts
    claims = {c["id"]: c for c in facts["claims"]}
    for phase in facts["phases"]:
        if any(claims[r]["meaning"] == "relative_slow" for r in phase["claims"]):
            assert phase["start_s"] >= 220
    assert catalog.baseline["included_seconds"] > 0
    assert movement[0].diagnostics["policies"]["movement"]["minimum_seconds"] == 10
    assert movement[0].diagnostics["policies"]["movement"]["slow_ratio"] == 0.5
    assert movement[0].diagnostics["policies"]["geometry_dictionary"]


def test_turn_pivot_does_not_inherit_pace_before_the_turn():
    claims = [
        {
            "id": "turn",
            "kind": "path",
            "meaning": "turn_left",
            "start_s": 0,
            "end_s": 30,
            "event_s": 20,
        },
        {"id": "slow", "kind": "pace", "meaning": "relative_slow", "start_s": 0, "end_s": 10},
    ]
    phases = phases_for(claims, 0, 30)
    assert phases == [
        {"start_s": 0, "end_s": 10, "claims": ["slow"]},
        {"start_s": 20, "end_s": 30, "claims": ["turn"]},
    ]
    request = {
        "movement": [
            {
                "id": "slot",
                "facts": {
                    "format": "diary-movement-material-v1",
                    "scene_at_s": 20,
                    "claims": claims,
                    "phases": phases,
                },
            }
        ]
    }
    wire, _ = activity_projection(request)
    first, second = wire["movement"]["materials"]
    assert "느리게" in first["changes"][0]["meaning"]
    assert "느림" not in second["meaning"]
    assert second["occurrence"] == "한 번의 방향 전환"
    assert "at_s" not in json.dumps(wire)


def test_revisited_coordinates_bind_to_different_times_and_notes_receive_movement():
    base, _ = prepared(note=True)
    notes = [s for s in base.board.scenes if s.core.kind == "user_record"]
    assert notes
    requests = [activity_jobs.action_job(base, s) for s in base.board.scenes]
    note_job = activity_jobs.action_job(base, notes[0])
    assert note_job.request["action"] is None and note_job.request["movement"]
    for job in filter(None, requests):
        for item in job.request["movement"]:
            window = item["facts"]["window"]
            at = item["facts"]["scene_at_s"]
            assert window["start_s"] <= at <= window["end_s"]


@pytest.mark.parametrize("pin,note", [(False, False), (True, False), (True, True)])
async def test_real_request_result_storage_and_whole_titles(pin, note):
    base, _ = prepared(pin=pin, note=note)
    fake = AsyncMock(side_effect=provider)
    result = await writing.write_cards(base.input.source, base, generate=fake)
    jobs = [j for j in result.jobs if j.stage == "action"]
    assert jobs and all(j.accepted for j in jobs)
    for job in jobs:
        assert job.llm_request == normalize("action", job.request).payload
        assert set(job.accepted.get("movement_ids", [])) <= {
            u["id"] for u in movement_uses(job.request)
        }
        assert len(job.request["movement"]) == 1
        wire = json.dumps(job.llm_request, ensure_ascii=False)
        assert not any(
            f'"{k}":' in wire
            for k in (
                "baseline_mps",
                "source_revision",
                "lat",
                "lng",
                "diagnostics",
                "from_s",
                "to_s",
                "at_s",
                "phases",
            )
        )
    titles = [j for j in result.jobs if j.stage == "title"]
    assert len(titles) == 1
    assert [c["body"] for c in titles[0].llm_request["context"]] == [
        c.body for c in result.bundle.scenes
    ]
    prepared_value = PreparedWalkDiary(base.input, base.plan.intermediate, base)
    saved = store_board(prepared_value, result.bundle, digest("generation"), writing=result)
    assert load_board(saved).bundle == result.bundle
    reused = replace(base, cached_jobs=tuple(j.model_dump(mode="json") for j in result.jobs))
    fake.reset_mock()
    again = await writing.write_cards(base.input.source, reused, generate=fake)
    fake.assert_not_awaited()
    assert again.bundle == result.bundle


async def test_missing_selected_movement_fails_before_external_call(monkeypatch):
    from daengs_backend.orchestration import diary

    base, _ = prepared()
    original = diary.normalize

    def damaged(stage, request):
        model = original(stage, request)
        if stage == "action" and request.get("movement"):
            model.payload["movement"]["materials"] = []
        return model

    monkeypatch.setattr(diary, "normalize", damaged)
    fake = AsyncMock(side_effect=provider)
    result = await writing.write_cards(base.input.source, base, generate=fake)
    assert not any(c.args[0] == "action" for c in fake.call_args_list)
    assert any(j.failure_code == "invalid_input" for j in result.jobs)
    assert any(c.writing.actions for c in result.bundle.scenes)


def test_gap_keeps_behavior_without_assigning_a_cross_gap_movement():
    from datetime import timedelta

    base, _ = prepared(gap=True)
    raw = base.input.source.model_dump(mode="json")
    anchor = raw["records"][0]["anchor"]
    at = base.input.source.started_at + timedelta(seconds=180)
    anchor["event_at"] = anchor["location_at"] = at.isoformat()
    updated = assemble_saved_base_board(
        replace(base.input, source=DiaryInput.model_validate(raw)),
        board_policy(3),
        slot_policy=base.slots.policy,
    )
    scene = next(s for s in updated.board.scenes if s.core.kind == "user_record")
    job = activity_jobs.action_job(updated, scene)
    assert job.request["action"] and not job.request.get("movement")
    assert any(
        d.reason == "movement_visit_match_unknown"
        for s in updated.slots.stamps
        if s.scene_id == scene.id
        for d in s.decisions
    )


async def test_delayed_space_preserves_frozen_motion_with_tight_total_budget(monkeypatch):
    from daengs_walk.diary.route import movement as diary_movement
    from tests.walk.diary.test_diary_card_writing import collect_with_sgis

    base, _ = prepared()
    base = assemble_saved_base_board(
        base.input, board_policy(3), slot_policy=SlotPolicy(movement={}, total_slots=1)
    )
    original = [[e for e in s.evidence if e.part == "motion"] for s in base.slots.stamps]
    assert all(original)
    monkeypatch.setattr(
        diary_movement,
        "prepare_movement",
        lambda *_: (_ for _ in ()).throw(AssertionError("movement recalculated")),
    )
    snapshot = await collect_with_sgis(base.board)
    after = with_scene_backgrounds(base, snapshot)
    assert [[e for e in s.evidence if e.part == "motion"] for s in after.slots.stamps] == original
    assert all(len(s.evidence) == 1 for s in after.slots.stamps)


def test_path_citation_cannot_suppress_a_pace_observation():
    from datetime import datetime, timedelta
    from types import SimpleNamespace

    from daengs_walk.diary.board.activity import covers_observation

    base, _ = prepared()
    scene = next(s for s in base.board.scenes if s.core.kind == "user_record")
    request = activity_jobs.action_job(base, scene).request
    uses = movement_uses(request)
    pace = next(u for u in uses if u["meaning"] == "relative_slow")
    at = datetime.fromisoformat(request["event_at"])
    observation = SimpleNamespace(
        kind="observed_slow",
        started_at=at + timedelta(seconds=pace["from_s"]),
        ended_at=at + timedelta(seconds=pace["to_s"]),
    )
    paths = [u["id"] for u in uses if u["kind"] == "path"]
    assert not covers_observation(request, paths, observation)
    assert covers_observation(request, [pace["id"]], observation)
    observation.ended_at += timedelta(seconds=1)
    assert not covers_observation(request, [pace["id"]], observation)


def test_equal_coordinates_on_outward_and_return_visits_get_different_progress():
    from copy import deepcopy

    base, verified = prepared()
    raw = base.input.source.model_dump(mode="json")
    template = raw["records"][0]
    raw["records"] = []
    for seconds in (20, 220):
        point = next(
            p
            for p in verified.evidence.accepted_points
            if (p.at - base.input.source.started_at).total_seconds() == seconds
        )
        record = deepcopy(template)
        record["ref"]["id"] = f"visit-{seconds}"
        record["anchor"] = observed_anchor(point).model_copy(update={"time_basis": "recorded_at"})
        raw["records"].append(record)
    changed = assemble_saved_base_board(
        replace(base.input, source=DiaryInput.model_validate(raw)),
        board_policy(3),
        slot_policy=base.slots.policy,
    )
    visits = [s for s in changed.board.scenes if s.core.kind == "user_record"]
    assert visits[0].anchor.point == visits[1].anchor.point
    meanings = [
        {u["meaning"] for u in movement_uses(activity_jobs.action_job(changed, s).request)}
        for s in visits
    ]
    assert "retrace" not in meanings[0] and "retrace" in meanings[1]


def test_missing_baseline_keeps_supported_path_without_normal_pace():
    base, verified = prepared(waypoints=[(0, 0, 0), (180, 0, 72)])
    catalog = prepare_movement(base.input.source, verified, MovementPolicy())
    assert catalog.baseline["baseline_mps"] is None
    assert catalog.baseline["excluded_seconds"] > 0
    assert catalog.claims and all(c["kind"] == "path" for c in catalog.claims)
    job = activity_jobs.action_job(base, base.board.scenes[0])
    wire = normalize("action", job.request).payload
    assert all(not p["changes"] and p["meaning"] for p in wire["movement"]["materials"])
    assert "정상" not in json.dumps(wire, ensure_ascii=False)


async def test_long_original_and_activity_fit_existing_app_body_limit():
    from tests.walk.diary.test_diary_card_writing import collect_with_sgis

    base, _ = prepared(note=True)
    raw = base.input.source.model_dump(mode="json")
    raw["records"][0]["content"]["text"] = "가" * 2000
    base = assemble_saved_base_board(
        replace(base.input, source=DiaryInput.model_validate(raw)),
        board_policy(3),
        slot_policy=base.slots.policy,
    )

    async def lengthy(stage, payload, schema):
        result = await provider(stage, payload, schema)
        if stage in {"space", "action"}:
            result["text"] = "나" * 220
            if stage == "space":
                result["evidence_ids"] = [payload["materials"][0]["id"]]
        return result

    result = await writing.write_cards(
        base.input.source, base, generate=lengthy, collector=collect_with_sgis
    )
    card = next(c for c in result.bundle.scenes if c.user_record)
    assert card.writing.original_text == "가" * 2000
    assert card.body.endswith("가" * 2000) and len(card.body) <= 2400
    assert card.writing.actions[0].text == "나" * 220
    assert not card.writing.space.text
    bound = with_scene_backgrounds(base, result.scene_backgrounds)
    value = PreparedWalkDiary(bound.input, bound.plan.intermediate, bound)
    assert (
        load_board(store_board(value, result.bundle, digest("long-note"), writing=result)).bundle
        == result.bundle
    )


async def test_unknown_activity_citation_falls_back_without_losing_record():
    base, _ = prepared()

    async def invalid(stage, payload, schema):
        result = await provider(stage, payload, schema)
        if stage == "action" and "movement" in payload:
            result["evidence_ids"] = ["m-foreign"]
        return result

    result = await writing.write_cards(base.input.source, base, generate=invalid)
    assert all(
        c.writing.actions[0].origin == "fallback" for c in result.bundle.scenes if c.writing.actions
    )
    assert any(c.writing.actions[0].action_id for c in result.bundle.scenes if c.writing.actions)
