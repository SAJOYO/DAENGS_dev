"""Meaning survives projection; acquisition bookkeeping cannot become writing content."""

import json
from copy import deepcopy
from datetime import timedelta

import pytest

from daengs_walk.diary.relational.brief_contracts import DeliveredMeaning, EventContext
from daengs_walk.diary.relational.contracts import CurrentMotion
from daengs_walk.diary.relational.current_action import build_action_brief
from daengs_walk.diary.relational.interval_writer_view import interval_writer_view
from daengs_walk.diary.relational.writer_meaning import (
    event_anchor_view,
    fact_view,
    present,
    route_view,
)
from daengs_walk.diary.relational.writer_view import publication_writer_view
from daengs_walk.diary.relational.writing_brief import (
    advance_brief_delivery,
    brief_writer_view,
    build_space_brief,
)
from tests.walk.diary.test_writing_brief import case, context, snapshot


def assert_internal_absent(value):
    wire = json.dumps(value, ensure_ascii=False)
    for token in (
        "recording_device",
        "semantic_status",
        "unverified",
        "등록",
        "조회",
        "source_fixes",
        "client_seq",
        "chain_index",
        "position_state",
        "allocation_at",
        "source_status",
        "detector_estimate",
        "INTERNAL-ONLY",
        "unconfirmed",
        "unknown",
        "unspecified",
        "causes_behavior",
        "same_object_time_change",
        "support_end_is_event_end",
    ):
        assert token not in wire


def test_spatial_relationship_and_memory_preserve_meaning_without_bookkeeping():
    walk, scenes, positions = case()
    a, b = snapshot(scenes[0], walk), snapshot(scenes[1], walk, cover="숲", distance=30)
    first, second = context(None, a, positions), context(a, b, positions)
    selected = DeliveredMeaning(context=first, evidence_ids=tuple(f.id for f in first.facts))
    memory = advance_brief_delivery(build_space_brief(first), selected)
    brief = build_space_brief(second, memory)
    before = brief.model_dump_json()
    request = brief_writer_view(brief)
    assert_internal_absent(request)
    assert brief.model_dump_json() == before
    assert "등록" in before and "unverified" in before
    relations = [r for rows in request["relation_slots"].values() for r in rows]
    assert any(r["result"] == "nearer" and r["axis"] == "object_distance" for r in relations)
    assert any(
        r["family"] == "land_cover" and r["result"] == "different_characteristics"
        for r in relations
    )
    area = next(f for f in request["available_facts"] if f["meaning"]["kind"] == "area_context")
    assert area["meaning"]["spatial_distribution"] == "dispersed"
    assert area["scope"] == {"kind": "surrounding_area", "distribution_of": "business_locations"}
    assert area["material_time"]["as_of"] == "2026-09"
    fact = second.facts[0]
    changed = fact.model_copy(
        update={
            "time_meaning": "INTERNAL-ONLY",
            "scope": fact.scope.model_copy(update={"description": "INTERNAL-ONLY"}),
        }
    )
    assert fact_view(changed) == fact_view(fact)
    assert "selected_route" not in request["delivery_memory"][0]
    assert "selected_relations" not in request["delivery_memory"][0]
    assert request["walk"]["selected_scene_count"] == 2
    for anchor in [
        request["current"],
        request["earlier"],
        *request["delivery_memory"][0]["anchors"],
    ]:
        assert "walk_started_at" not in anchor["position"]
        assert "selected_scene_count" not in anchor["position"]
        assert "point" not in anchor


def test_current_dog_event_keeps_time_shape_and_pace_without_source_actor():
    walk, scenes, positions = case()
    ctx = context(None, snapshot(scenes[0], walk), positions)
    action = build_action_brief(scenes[0], walk, [("dog", "보리")], [], ctx)
    motion = CurrentMotion(
        id="gait",
        meaning="이번 산책 기준보다 느린 걸음",
        from_pin_s=-10,
        to_pin_s=15,
        event_at_pin_s=0,
        relation="INTERNAL-ONLY",
    )
    option = EventContext(
        for_event_id=action.required_event.id,
        kind="current_gait",
        evidence=motion,
        subject="recording_device",
    )
    action = action.model_copy(update={"context_options": (*action.context_options, option)})
    before = action.model_dump_json()
    request = brief_writer_view(action)
    assert_internal_absent(request)
    assert request["required_event"]["actor"] == {
        "entity_type": "dog",
        "pet_id": "dog",
        "name": "보리",
    }
    assert request["required_event"]["behavior"] == "sniffing"
    event = request["required_event"]["anchor"]
    assert event["event_at"] == action.required_event.anchor.event_at.isoformat()
    assert event["position_basis"] == action.required_event.anchor.method
    gait = request["context_options"][-1]
    assert gait["for_event_id"] == request["required_event"]["id"]
    assert gait["evidence"]["relative_to_event"] == {
        "start_seconds": -10,
        "end_seconds": 15,
        "event_seconds": 0,
    }
    assert gait["evidence"]["relationship"] == "walk_movement_coincident_with_event"
    assert action.model_dump_json() == before
    legacy = publication_writer_view(action, "single-writing-brief-v1")
    assert legacy["context_options"][-1]["subject"] == "recording_device"


def test_route_keeps_gaps_and_distances_without_device_subject():
    from daengs_walk.diary.relational.brief_contracts import RouteInterval

    route = RouteInterval(
        id="route",
        status="partial",
        started_at="2026-09-15T09:00:00Z",
        ended_at="2026-09-15T09:02:00Z",
        elapsed_seconds=120,
        observed_seconds=90,
        observed_distance_m=80,
        moving_distance_m=75,
        uncovered_intervals=[{"start": "2026-09-15T09:01:00Z", "end": "2026-09-15T09:01:30Z"}],
    )
    request = route_view(route)
    assert_internal_absent(request)
    assert request["status"] == "partial" and request["covered_seconds"] == 90
    assert request["distance_m"] == 80 and len(request["gaps"]) == 1
    assert request["scope"] == {"kind": "walk_interval", "spatial_support": "endpoints"}
    assert route.subject == "recording_device"
    connected = route.model_copy(
        update={
            "status": "connected",
            "uncovered_intervals": (),
            "observed_seconds": 120,
            "observed_distance_m": 0,
            "moving_distance_m": 0,
        }
    )
    connected = type(route).model_validate(connected.model_dump())
    assert route_view(connected)["gaps"] == []
    assert route_view(connected)["distance_m"] == 0


def test_interval_projection_keeps_uncertainty_and_retrace_but_not_allocation():
    relation = {
        "id": "segment1",
        "kind": "retrace",
        "subject": "recording_device",
        "support_started_at": "2026-09-15T09:00:00Z",
        "support_ended_at": "2026-09-15T09:01:00Z",
        "representative_event_at": "2026-09-15T09:00:30Z",
        "event_time_basis": "detector_estimate",
        "source_status": "candidate",
        "allocation_at": "INTERNAL-ONLY",
        "central": True,
        "prior_path": {"past_from_seq": 1, "past_to_seq": 20, "matched_reverse_m": 42},
    }
    before = deepcopy(relation)
    request = interval_writer_view(relation)
    assert_internal_absent(request)
    assert request["certainty"] == "inferred"
    assert request["event_time_precision"] == "estimated"
    assert request["prior_path"]["matched_reverse_m"] == 42
    assert "past_from_seq" not in request["prior_path"]
    assert before == relation
    interval_only = interval_writer_view(
        {
            **relation,
            "representative_event_at": None,
            "event_time_basis": None,
            "source_status": None,
        }
    )
    assert (
        not {"representative_event_at", "event_time_precision", "certainty"} & interval_only.keys()
    )


def test_missing_optional_facts_are_omitted_without_losing_zero_values():
    walk, scenes, positions = case(pet_id=None)
    ctx = context(None, snapshot(scenes[0], walk, distance=0), positions)
    request = brief_writer_view(build_space_brief(ctx))
    assert (
        not {"earlier", "route", "connection", "delivery_memory", "relation_slots"} & request.keys()
    )
    assert request["relation_ids"] == []  # Explicit citation allowlist, not a missing fact.
    park = next(f for f in ctx.facts if f.meaning.kind == "surrounding_object")
    undated = park.model_copy(update={"reference_date": None, "observed_at": None})
    projected = fact_view(undated)
    assert "material_time" not in projected
    assert "park_type" not in projected["meaning"] and "area_m2" not in projected["meaning"]
    assert projected["meaning"]["distance_m"] == 0
    action = build_action_brief(scenes[0], walk, [], [], ctx)
    action = action.model_copy(update={"context_options": ()})
    request = brief_writer_view(action)
    assert "context_options" not in request
    assert request["required_event"]["actor"] == {"entity_type": "dog"}
    assert present(zero=0, real_false=False, missing=None, blank={}, empty=[]) == {
        "zero": 0,
        "real_false": False,
    }


def test_location_age_and_endpoint_tolerance_survive_without_raw_coordinates():
    _, scenes, _ = case()
    anchor = scenes[0].anchor.model_copy(
        update={
            "method": "last_known",
            "location_at": scenes[0].anchor.event_at - timedelta(seconds=12),
            "accuracy_m": 0,
        }
    )
    result = event_anchor_view(anchor)
    assert result["location_at"] == anchor.location_at.isoformat()
    assert result["position_basis"] == "last_known" and result["accuracy_m"] == 0
    assert "point" not in result
    relation = {
        "id": "end",
        "kind": "end_near_start",
        "start_anchor": anchor.model_dump(mode="json"),
        "end_anchor": anchor.model_dump(mode="json"),
        "distance_m": 0,
        "tolerance_m": 5,
    }
    result = interval_writer_view(relation)
    assert_internal_absent(result)
    assert result["distance_m"] == 0 and result["tolerance_m"] == 5
    assert result["scope"]["kind"] == "session_endpoints"


def test_unknown_policy_cannot_reinterpret_saved_input():
    walk, scenes, positions = case()
    brief = build_space_brief(context(None, snapshot(scenes[0], walk), positions))
    with pytest.raises(ValueError, match="unsupported"):
        publication_writer_view(brief, "unknown")
