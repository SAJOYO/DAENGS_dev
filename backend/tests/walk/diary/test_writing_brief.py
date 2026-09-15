"""New domain contracts, with real source/snapshot comparison and no model or DB calls."""

import json
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from daengs_walk.diary.contracts.input import Behavior
from daengs_walk.diary.relational.brief_contracts import (
    ActionWritingBrief,
    DeliveredMeaning,
    NarrativeSpaceContext,
    SpaceWritingBrief,
)
from daengs_walk.diary.relational.current_action import build_action_brief
from daengs_walk.diary.relational.narrative_space import build_space_context
from daengs_walk.diary.relational.relations.registry import collect_spatial_comparisons
from daengs_walk.diary.relational.scene_comparison_contracts import (
    SceneSnapshot,
    SpaceComparisonInput,
)
from daengs_walk.diary.relational.walk_phase import ScenePosition, WalkTimeline, scene_positions
from daengs_walk.diary.relational.writing_brief import (
    advance_brief_delivery,
    brief_writer_view,
    build_space_brief,
    space_work_reason,
)
from tests.walk.support.diary import record, source


def snapshot(
    scene, walk, *, count=12, mix="여러 업종 혼합", road="매헌로", cover="길", distance=100
):
    point = scene.anchor.point.model_dump(mode="json")
    values = {
        "road": {"name": road},
        "land_cover": {"피복": cover, "classification": {"피복": cover}, "layer": "land-v1"},
        "surrounding_object": {
            "name": "마방공원",
            "registered_point": {"lat": 37.5, "lng": 127.01},
            "distance_m": distance,
            "catalog_complete": True,
        },
        "area_context": {
            "composition": {"업종구성": mix, "조회영역_등록분포": "등록 지점이 흩어져 있음"},
            "query": {"kind": "query_circle", "point": point, "radius_m": 250},
            "registered_count": count,
            "registered_sites": ["shop-a", "shop-b"],
            "classification_policy": "commerce-v1",
            "source_hash": str(count),
        },
    }
    scope = {
        "road": "record_point",
        "land_cover": "record_point",
        "surrounding_object": "registered_point",
        "area_context": "query_area",
    }
    return SceneSnapshot(
        scene_id=scene.id,
        walk_id=walk.revision(),
        recorded_at=scene.anchor.event_at,
        point=scene.anchor.point,
        position_basis=scene.anchor.method,
        accuracy_m=scene.anchor.accuracy_m,
        collection={k: "complete" for k in values},
        facts=tuple(
            {
                "id": scene.id + ":" + k,
                "family": k,
                "value": v,
                "scope": {
                    "kind": scope[k],
                    "description": "해당 자료의 범위만 설명",
                    "coverage_key": scene.id,
                },
                "subject_key": "parks:1"
                if k == "surrounding_object"
                else "commerce:area:" + scene.id,
                "reference_date": "2026-09",
                "source_refs": ["audit-source:" + k],
                "time_meaning": "지도·등록 기준 시점; 산책 시각의 직접 관측 아님",
            }
            for k, v in values.items()
        ),
    )


def context(a, b, positions):
    connection = (
        None
        if a is None
        else {
            "earlier_scene_id": a.scene_id,
            "current_scene_id": b.scene_id,
            "elapsed_seconds": (b.recorded_at - a.recorded_at).total_seconds(),
            "route_status": "unavailable",
            "scope": "두 기록의 경과 시간; 중간 이동 미확인",
        }
    )
    request = SpaceComparisonInput(
        current=b,
        earlier=a,
        connection=connection,
        relation_slots=collect_spatial_comparisons(b, a),
    )
    return build_space_context(request, positions)


def case(*, pet_id="dog"):
    pins = [record("pin1", "05"), record("pin2", "10")]
    pins = [
        p.model_copy(update={"content": Behavior(kind="behavior", code="sniffing", pet_id=pet_id)})
        for p in pins
    ]
    pins[1] = pins[1].model_copy(
        update={
            "anchor": pins[1].anchor.model_copy(
                update={"point": pins[1].anchor.point.model_copy(update={"lat": 37.501})}
            )
        }
    )
    walk = source(*pins)
    scenes = [
        SimpleNamespace(id=f"s{i}", core=SimpleNamespace(record=p), anchor=p.anchor)
        for i, p in enumerate(pins, 1)
    ]
    return walk, scenes, scene_positions(walk, scenes)


def accepted(ctx):
    return DeliveredMeaning(context=ctx, evidence_ids=(ctx.current_facts[0].id,))


def test_registration_churn_uses_same_meaning_for_plan_memory_and_request():
    walk, scenes, positions = case()
    a, b = snapshot(scenes[0], walk), snapshot(scenes[1], walk, count=91)
    before = a.model_dump_json(), b.model_dump_json()
    first, second = context(None, a, positions), context(a, b, positions)
    assert first.signature == second.signature
    state = advance_brief_delivery(build_space_brief(first), accepted(first))
    brief = build_space_brief(second, state)
    assert space_work_reason(brief) == "maintain"
    assert second.relation_slots.area_context[0].result == "same_characteristics"
    raw_ids = {r.id for r in collect_raw(a, b).relation_slots.all_relations()}
    assert not raw_ids & set(brief.relation_ids)
    wire = json.dumps(brief_writer_view(brief), ensure_ascii=False)
    for forbidden in (
        "registered_count",
        "registered_sites",
        "source_hash",
        "changed_fields",
        "source_bindings",
        "audit-source:",
    ):
        assert forbidden not in wire
    assert "업종" in wire  # Keep actual area meaning, not merely hide the whole family.
    assert before == (a.model_dump_json(), b.model_dump_json())
    assert SpaceWritingBrief.model_validate_json(brief.model_dump_json()) == brief


def collect_raw(a, b):
    return SpaceComparisonInput(
        current=b,
        earlier=a,
        relation_slots=collect_spatial_comparisons(b, a),
        connection={
            "earlier_scene_id": a.scene_id,
            "current_scene_id": b.scene_id,
            "elapsed_seconds": (b.recorded_at - a.recorded_at).total_seconds(),
            "route_status": "unavailable",
            "scope": "양 끝 기록",
        },
    )


@pytest.mark.parametrize(
    "change,slot,result",
    [
        ({"road": "강남대로"}, "background", "different_characteristics"),
        ({"cover": "숲"}, "background", "different_characteristics"),
        ({"mix": "음식점 중심"}, "area_context", "different_characteristics"),
        ({"distance": 45}, "proximity", "nearer"),
        ({"distance": 160}, "proximity", "farther"),
    ],
)
def test_actual_spatial_difference_changes_the_shared_context(change, slot, result):
    walk, scenes, positions = case()
    a, b = snapshot(scenes[0], walk), snapshot(scenes[1], walk, **change)
    ctx = context(a, b, positions)
    assert result in {r.result for r in getattr(ctx.relation_slots, slot)}
    assert ctx.signature != ctx.signature_for(a.scene_id)
    assert space_work_reason(build_space_brief(ctx)) == "compare"
    assert all(not r.establishes_temporal_change for r in ctx.relation_slots.all_relations())


def test_failed_introduction_recovers_but_success_does_not_force_all_facts():
    walk, scenes, positions = case()
    a, b = snapshot(scenes[0], walk), snapshot(scenes[1], walk)
    first, second = context(None, a, positions), context(a, b, positions)
    failed = advance_brief_delivery(build_space_brief(first), None)
    assert space_work_reason(build_space_brief(second, failed)) == "recover_introduction"
    success = advance_brief_delivery(build_space_brief(first), accepted(first))
    brief = build_space_brief(second, success)
    assert space_work_reason(brief) == "maintain"
    assert len(brief_writer_view(brief)["delivery_memory"][0]["selected_facts"]) == 1


def test_missing_current_data_is_not_disappearance_and_incomparable_area_is_not_change():
    walk, scenes, positions = case()
    a, b = snapshot(scenes[0], walk), snapshot(scenes[1], walk)
    missing = b.model_dump(mode="json")
    missing.update(facts=[], collection={k: "failed" for k in b.collection})
    ctx = context(a, SceneSnapshot.model_validate(missing), positions)
    assert not ctx.relation_slots.all_relations()
    assert space_work_reason(build_space_brief(ctx)) == "unavailable"
    changed = b.model_dump(mode="json")
    changed["facts"][-1]["reference_date"] = "2026-08"
    ctx = context(a, SceneSnapshot.model_validate(changed), positions)
    assert not ctx.relation_slots.area_context
    assert any("not_comparable" in v for v in ctx.omitted_sources.values())


def test_missing_one_area_characteristic_does_not_invent_a_contrast():
    walk, scenes, positions = case()
    a, b = snapshot(scenes[0], walk), snapshot(scenes[1], walk)
    data = b.model_dump(mode="json")
    data["facts"][-1]["value"]["composition"].pop("조회영역_등록분포")
    ctx = context(a, SceneSnapshot.model_validate(data), positions)
    assert not ctx.relation_slots.area_context
    assert ctx.current_facts[-1].meaning.business_mix


@pytest.mark.parametrize(
    "elapsed,phase",
    [
        (0, "departure"),
        (120, "departure"),
        (600, "in_progress"),
        (2300, "closing"),
        (2400, "closing"),
    ],
)
def test_phase_uses_session_time_not_selected_scene_number(elapsed, phase):
    walk, _, _ = case()
    position = ScenePosition(
        scene_id="first-selected",
        selected_scene_number=1,
        selected_scene_count=1,
        timeline=WalkTimeline(
            source_revision=walk.revision(), started_at=walk.started_at, ended_at=walk.ended_at
        ),
        recorded_at=walk.started_at + timedelta(seconds=elapsed),
    )
    view = position.writer_view()
    assert view["phase"] == phase
    assert view["is_walk_start"] == (elapsed == 0)
    assert view["is_walk_end"] == (elapsed == 2400)


def test_short_walk_overlap_and_invalid_time():
    walk, _, _ = case()
    timeline = WalkTimeline(
        source_revision=walk.revision(),
        started_at=walk.started_at,
        ended_at=walk.started_at + timedelta(seconds=100),
    )
    for seconds, phase in [(0, "departure"), (50, "in_progress"), (100, "closing")]:
        value = ScenePosition(
            scene_id="s",
            selected_scene_number=1,
            selected_scene_count=1,
            timeline=timeline,
            recorded_at=walk.started_at + timedelta(seconds=seconds),
        )
        assert value.phase == phase
    with pytest.raises(ValidationError, match="outside walk"):
        ScenePosition(
            scene_id="s",
            selected_scene_number=1,
            selected_scene_count=1,
            timeline=timeline,
            recorded_at=walk.ended_at,
        )


@pytest.mark.parametrize("pet_id", ["dog", None])
def test_real_pin_identity_is_required_and_repeated_pins_remain_distinct(pet_id):
    walk, scenes, positions = case(pet_id=pet_id)
    contexts = [context(None, snapshot(s, walk), positions) for s in scenes]
    briefs = [
        build_action_brief(s, walk, [("dog", "보리")], [], c) for s, c in zip(scenes, contexts)
    ]
    assert briefs[0].required_event.id != briefs[1].required_event.id
    for brief in briefs:
        event = brief.required_event
        assert event.actor.entity_type == "dog" and event.actor.pet_id == pet_id
        assert event.actor.name == ("보리" if pet_id else None)
        assert event.behavior == "sniffing"
        assert all(o.for_event_id == event.id for o in brief.context_options)
        assert ActionWritingBrief.model_validate_json(brief.model_dump_json()) == brief
        wire = brief_writer_view(brief)
        assert (
            not {"narration", "companions", "earlier", "delivery_memory", "source_record"}
            & wire.keys()
        )
        bad = brief.model_dump(mode="json")
        bad["required_event"]["actor"]["entity_type"] = "guardian"
        with pytest.raises(ValidationError):
            ActionWritingBrief.model_validate(bad)
        bad = brief.model_dump(mode="json")
        bad["context_options"][0]["evidence"]["scene_id"] = "previous"
        with pytest.raises(ValidationError, match="past or future"):
            ActionWritingBrief.model_validate(bad)


def test_no_behavior_pin_means_no_action_and_foreign_event_is_rejected():
    walk, scenes, positions = case()
    ctx = context(None, snapshot(scenes[0], walk), positions)
    scene = SimpleNamespace(
        id=scenes[0].id, anchor=scenes[0].anchor, core=SimpleNamespace(record=record())
    )
    assert build_action_brief(scene, walk, [], [], ctx) is None
    alien = deepcopy(scenes[0])
    alien.core.record = alien.core.record.model_copy(update={"ref": record("another").ref})
    with pytest.raises(ValueError, match="source record version"):
        build_action_brief(alien, walk, [], [], ctx)


def test_invalid_relation_endpoint_and_memory_from_future_are_rejected():
    walk, scenes, positions = case()
    a, b = snapshot(scenes[0], walk), snapshot(scenes[1], walk, cover="숲")
    ctx = context(a, b, positions)
    changed = ctx.model_dump(mode="json")
    changed["relation_slots"]["background"][0]["earlier_evidence_ids"] = [ctx.current_facts[0].id]
    with pytest.raises(ValidationError, match="endpoint"):
        NarrativeSpaceContext.model_validate(changed)
    state = advance_brief_delivery(build_space_brief(ctx), accepted(ctx))
    with pytest.raises(ValidationError, match="earlier scenes"):
        build_space_brief(context(None, a, positions), state)


@pytest.mark.parametrize("bounds", [(-10, 10), (0, 10), (-10, 0), (1, 10)])
def test_motion_context_must_contain_the_current_pin(bounds):
    from daengs_walk.diary.relational.brief_contracts import EventContext
    from daengs_walk.diary.relational.contracts import CurrentMotion

    walk, scenes, positions = case()
    ctx = context(None, snapshot(scenes[0], walk), positions)
    brief = build_action_brief(scenes[0], walk, [("dog", "보리")], [], ctx)
    data = {
        "id": "motion",
        "meaning": "이번 산책 기준보다 느린 걸음",
        "from_pin_s": bounds[0],
        "to_pin_s": bounds[1],
        "event_at_pin_s": 0,
        "relation": "동시점 산책 이동",
    }
    if not bounds[0] <= 0 < bounds[1]:
        with pytest.raises(ValidationError, match="contain the current pin"):
            CurrentMotion(**data)
    else:
        option = EventContext(
            for_event_id=brief.required_event.id,
            evidence=CurrentMotion(**data),
            kind="current_gait",
            subject="recording_device",
        )
        updated = ActionWritingBrief(
            position=brief.position,
            required_event=brief.required_event,
            context_options=(*brief.context_options, option),
        )
        assert (
            brief_writer_view(updated)["context_options"][-1]["for_event_id"]
            == brief.required_event.id
        )


def test_same_name_and_distance_do_not_merge_different_parks():
    walk, scenes, positions = case()
    a, b = snapshot(scenes[0], walk), snapshot(scenes[1], walk)
    data = b.model_dump(mode="json")
    data["facts"][2]["subject_key"] = "parks:other"
    ctx = context(a, SceneSnapshot.model_validate(data), positions)
    assert ctx.signature != ctx.signature_for(a.scene_id)
    assert not ctx.relation_slots.proximity
    assert space_work_reason(build_space_brief(ctx)) == "current_context"
    assert "parks:other" not in json.dumps(brief_writer_view(build_space_brief(ctx)))
