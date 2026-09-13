"""Port equivalence, finite meanings and per-scene pattern relations."""

import json
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from daengs_backend.config import settings
from daengs_backend.schemas.walk_diary_slots import SlotPreviewRequest
from daengs_backend.services import walk_diary_slots as preview_service
from daengs_backend.services.walk_diary_base_board import assemble_saved_base_board
from daengs_backend.services.walk_diary_input import InputAssembly
from daengs_backend.services.walk_diary_observations import ObservationSource
from daengs_backend.services.walk_diary_slot_writing import slot_payload
from daengs_evals.diary_slots_demo import demo_input
from daengs_walk.diary_board import VerifiedBoardRoute
from daengs_walk.diary_board_selection import observed_anchor
from daengs_walk.diary_input import DiaryInput, digest
from daengs_walk.diary_observations import build_observation_pool
from daengs_walk.diary_route_geometry import RoutePatternPolicy, extract_route_patterns
from daengs_walk.diary_route_normalize import build_patterns
from daengs_walk.diary_route_patterns import RoutePatternMaterial
from daengs_walk.diary_scene_input import scene_materials
from daengs_walk.diary_slots import SlotPolicy, prepare_board_slots, prepare_slot_preview
from daengs_walk.evidence import analyze_walk
from tests.walk.support.base_board import policy as board_policy
from tests.walk.support.route_patterns import scenarios

GOLD = (
    Path(__file__).resolve().parents[3] / "evals/walk-diary/route-patterns-v1/geo-geometry-v1.json"
)


def input_case(name="right", seq=18, *, pin=True):
    original, _, _ = demo_input()
    sample = scenarios()[name]["source"]
    computed = analyze_walk(
        uuid.UUID(original.walk_id), sample.started_at, sample.ended_at, sample.points
    )
    version = original.route.model_copy(update={"input_fingerprint": digest(sample)})
    raw = original.model_dump(mode="json")
    raw.update(
        started_at=sample.started_at,
        ended_at=sample.ended_at,
        route=version,
        observations=build_observation_pool(computed, version).observations,
        backgrounds=[],
        selected_background_ids=[],
        records=[],
    )
    if pin:
        record = original.records[0].model_dump(mode="json")
        record["content"] = {"kind": "behavior", "code": "sniffing", "pet_id": None}
        record["anchor"] = observed_anchor(sample.points[seq]).model_copy(
            update={"time_basis": "recorded_at"}
        )
        record["pin_payload"] = None
        raw["records"] = [record]
    return DiaryInput.model_validate(raw), VerifiedBoardRoute(version, computed), sample


def preview(name="right", seq=18, **options):
    source, verified, _ = input_case(name, seq)
    policy = SlotPolicy(route_patterns={}, motion_slots=4, **options)
    return prepare_slot_preview(source, policy, board_policy(1), route=verified)


def pin_stamp(value):
    index = next(i for i, s in enumerate(value.scenes) if s.core.kind == "user_record")
    return value.stamps[index]


def pattern_evidence(stamp):
    return [e for e in stamp.evidence if e.facts.get("format") == "route-pattern-material-v1"]


@pytest.mark.parametrize("name", list(scenarios()))
def test_original_geo_geometry_has_identical_measurements_support_and_quality(name):
    source = scenarios()[name]["source"]
    computed = analyze_walk(uuid.UUID(int=1), source.started_at, source.ended_at, source.points)
    actual = extract_route_patterns(computed)
    expected = json.loads(GOLD.read_text(encoding="utf-8"))[name]
    canonical = expected.pop("canonical_quality")
    text = (
        json.dumps(expected)
        .replace('"how_run_index"', '"pattern_run_index"')
        .replace('"how"', '"route_pattern"')
    )
    assert actual == json.loads(text)
    assert computed.quality.model_dump() == canonical


def test_finite_dictionary_and_source_policy_bind_every_candidate():
    source = scenarios()["out_back"]["source"]
    result = build_patterns(source)
    assert {m.case_id for m in result.materials} == {"straight_run", "turn_reverse", "retrace"}
    changed = build_patterns(source, RoutePatternPolicy(simplify_m=9))
    assert result.source_revision == changed.source_revision
    assert {m.id for m in result.materials}.isdisjoint(m.id for m in changed.materials)
    raw = result.materials[0].model_dump(mode="json")
    raw["material"] = {"행동": "냄새를 맡음"}
    with pytest.raises(ValueError, match="finite dictionary"):
        RoutePatternMaterial.model_validate(raw)


def test_turn_support_is_not_applied_as_a_turn_over_the_whole_leg():
    corner = pin_stamp(preview())
    assert {e.facts["case_id"] for e in pattern_evidence(corner)} == {"turn_right", "straight_run"}
    assert {e.facts["relation"]["kind"] for e in pattern_evidence(corner)} == {
        "starts_at_scene_point",
        "ends_at_scene_point",
        "near_observed_turn_vertex",
    }
    away = pin_stamp(preview(seq=5))
    assert {e.facts["case_id"] for e in pattern_evidence(away)} == {"straight_run"}
    assert any(d.reason == "outside_turn_focus" for d in away.decisions)


def test_straight_and_retrace_can_coexist_with_their_distinct_relations():
    stamp = pin_stamp(preview("out_back", 30))
    assert {e.facts["case_id"] for e in pattern_evidence(stamp)} == {"straight_run", "retrace"}
    assert all(e.facts["subject"] == "recording_device" for e in pattern_evidence(stamp))


def test_pattern_capacity_is_a_separate_decision_and_never_drops_the_action_anchor():
    source, verified, _ = input_case()
    value = prepare_slot_preview(
        source, SlotPolicy(route_patterns={}), board_policy(1), route=verified
    )
    stamp = pin_stamp(value)
    assert len(pattern_evidence(stamp)) == 1
    assert sum(d.admission == "part_capacity" for d in stamp.decisions) == 2
    assert (
        next(s for s in value.scenes if s.core.kind == "user_record").core.record.content.code
        == "sniffing"
    )


@pytest.mark.parametrize("change", ["unlocated", "provisional", "stale", "different_place"])
def test_unmatched_record_does_not_receive_a_pattern_from_nearby_time(change):
    source, verified, _ = input_case()
    raw = source.model_dump(mode="json")
    anchor = raw["records"][0]["anchor"]
    if change == "unlocated":
        anchor.update(
            point=None,
            location_at=None,
            method="none",
            position_state="unlocated",
            source_fixes=[],
            accuracy_m=None,
        )
    elif change == "provisional":
        anchor["position_state"] = "provisional"
    elif change == "stale":
        anchor["location_at"] = (source.started_at + timedelta(seconds=5)).isoformat()
        anchor["source_fixes"] = []
        anchor["method"] = "last_known"
    else:
        anchor["point"]["lat"] += 0.01
        anchor["source_fixes"] = []
    value = prepare_slot_preview(
        DiaryInput.model_validate(raw),
        SlotPolicy(route_patterns={}),
        board_policy(1),
        route=verified,
    )
    assert not pattern_evidence(pin_stamp(value))


def test_pattern_scene_without_action_pin_and_writer_projection_keep_only_meaning():
    source, verified, _ = input_case(pin=False)
    value = prepare_slot_preview(
        source, SlotPolicy(route_patterns={}, motion_slots=4), board_policy(3), route=verified
    )
    assert any(pattern_evidence(s) for s in value.stamps)
    assert not source.records
    board_slots = prepare_board_slots(source, value.base_board, value.policy, route=verified)
    payload = slot_payload(value.base_board, board_slots)
    facts = [
        e["facts"]
        for s in payload["scenes"]
        for e in scene_materials(s)
        if "material" in e["facts"]
    ]
    assert facts and any("경로 형태" in f["material"] for f in facts)
    for f in facts:
        assert set(f) == {"material", "relation", "subject", "action_meaning"}


def test_special_moment_does_not_gain_route_pattern_materials():
    source, verified, _ = input_case()
    raw = source.model_dump(mode="json")
    raw["records"][0]["content"] = {"kind": "note", "text": "  내가 직접 남긴 순간.  "}
    value = prepare_slot_preview(
        DiaryInput.model_validate(raw),
        SlotPolicy(route_patterns={}),
        board_policy(1),
        route=verified,
    )
    assert not pattern_evidence(pin_stamp(value))


def test_pattern_quality_break_does_not_rejoin_a_corner_or_a_stay():
    source = scenarios()["right"]["source"]
    points = list(source.points)
    points[18] = points[18].model_copy(update={"accuracy_m": 30})
    result = build_patterns(source.model_copy(update={"points": tuple(points)}))
    assert not any(m.kind == "turn" for m in result.materials)
    assert len(result.quality_audit) == 2
    assert {a["reason"] for a in result.quality_audit} == {"route_pattern_accuracy_rejection"}


async def test_configured_preview_and_actual_preparation_use_same_policy_and_stamps(monkeypatch):
    source, verified, _ = input_case()
    assembled = InputAssembly(
        source, (), ObservationSource(verified.version, evidence=verified.evidence)
    )
    monkeypatch.setattr(settings, "walk_diary_route_patterns_enabled", True)
    monkeypatch.setattr(preview_service, "read_input", AsyncMock(return_value=assembled))
    saved = assemble_saved_base_board(assembled, board_policy(1))
    result = await preview_service.preview_saved_slots(
        AsyncMock(), None, None, SlotPreviewRequest(target_scene_count=1, generate=False)
    )
    assert result.preview.policy.route_patterns is not None
    assert result.preview.stamps == saved.slots.stamps
    assert result.preview.revision == saved.slots.revision()
    assert pattern_evidence(pin_stamp(result.preview))


def test_old_slot_policy_serializes_without_new_null_field():
    raw = SlotPolicy().model_dump(mode="json")
    assert "route_patterns" not in raw
    assert raw == SlotPolicy.model_validate(raw).model_dump(mode="json")
