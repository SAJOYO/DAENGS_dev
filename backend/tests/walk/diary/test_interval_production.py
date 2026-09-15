"""Real preparation -> transport -> accepted memory -> frozen read, without provider calls."""

import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from itertools import pairwise

import pytest

from daengs_backend.services.walk_diary.collection import service as collection
from daengs_backend.services.walk_diary.preparation.relational import prepare_relational_diary
from daengs_backend.services.walk_diary.relational_execution import RelationalExecutionPolicy
from daengs_backend.services.walk_diary.runtime import write_relational_board
from daengs_backend.services.walk_diary.storage.relational import read_skeleton, save_skeleton
from daengs_backend.services.walk_diary.writing.relational import validate_prepared
from daengs_walk.diary.relational.brief_contracts import SpaceWritingBrief
from daengs_walk.diary.relational.brief_response import resolve_brief_answer
from daengs_walk.diary.relational.interval_materials import attach_interval_materials
from daengs_walk.diary.relational.interval_sources import IntervalSources
from daengs_walk.diary.relational.writer_view import writer_view
from daengs_walk.diary.space.materials import AreaInput
from daengs_walk.value_contracts import digest
from tests.walk.diary.test_brief_execution import answer
from tests.walk.diary.test_diary_activity import prepared as activity
from tests.walk.diary.test_diary_space_integration import public_collector  # noqa: F401
from tests.walk.diary.test_diary_space_materials import page, park


async def test_retrace_excluded_from_wire_schema_and_answer_but_source_is_preserved(collect_stable_target):
    from daengs_walk.diary.relational.brief_response import brief_response_schema
    from daengs_walk.diary.relational.writer_view import publication_writer_view

    prepared = await collect_stable_target(activity()[0])
    contexts = [f["narrative_context"] for f in prepared["snapshot"]["frames"]]
    context = next(c for c in reversed(contexts) if any(
        f["case"] == "route_retrace" for f in c["interval_relations"]["flows"]
    ))
    brief = SpaceWritingBrief(context=context)
    before = brief.model_dump_json()
    hidden = {f.id for f in brief.context.interval_relations.flows if f.case == "route_retrace"}
    new = writer_view(brief)
    schema = brief_response_schema(brief)
    assert hidden.isdisjoint(new["citation_ids"] + new["relation_ids"])
    assert all(i not in json.dumps([new, schema]) for i in hidden)
    assert "retracing" not in json.dumps(new)
    assert new["citation_ids"] == schema["properties"]["evidence_ids"]["items"]["enum"]
    assert new["relation_ids"] == schema["properties"]["relation_ids"]["items"].get("enum", [])
    old = publication_writer_view(brief, "single-writing-brief-v5")
    assert hidden <= set(old["citation_ids"])
    assert "retracing" in json.dumps(old)
    answer = {"text": "왔던 길을 되짚었다.", "focus": "이동", "evidence_ids": [next(iter(hidden))], "relation_ids": []}
    assert resolve_brief_answer(brief, answer, "single-writing-brief-v5")
    with pytest.raises(ValueError, match="evidence"):
        resolve_brief_answer(brief, answer)
    answer["evidence_ids"] = [new["citation_ids"][0]]
    answer["relation_ids"] = list(hidden)
    with pytest.raises(ValueError, match="relation"):
        resolve_brief_answer(brief, answer)
    assert brief.model_dump_json() == before


def test_previous_retrace_selection_is_not_reintroduced_by_memory_projection():
    from daengs_walk.diary.relational.writer_material_policy import omit_retrace

    request = {"delivery_memory": [{"selected_journey_relations": [
        {"id": "old-retrace", "relationship": "retracing", "prior_path": {"x": 1}},
        {"id": "distance", "relationship": "drawing_closer"},
    ]}]}
    projected = omit_retrace(request, set())
    assert projected["delivery_memory"][0]["selected_journey_relations"] == [
        {"id": "distance", "relationship": "drawing_closer"},
    ]


@pytest.fixture
def collect_stable_target(public_collector, monkeypatch):  # noqa: F811
    async def collect(base, *, scene_ids=None):
        point = base.input.observation_source.evidence.accepted_points[0]

        def cached(kind, query, radius, path):
            if kind == "commerce":
                raise ValueError("no covering catalog")
            row = {
                **park(1),
                "latitude": point.lat + 120 / 111320,
                "longitude": point.lng,
                "parkAr": "4500",
            }
            return AreaInput(
                query_point=query, radius_m=radius, pages=(page([row], "park"),)
            ), datetime(2026, 9, 1, tzinfo=UTC)

        monkeypatch.setattr(collection, "cached_area", cached)
        backgrounds = await public_collector(base.board)
        return prepare_relational_diary(
            replace(base, scene_backgrounds=backgrounds), scene_ids=scene_ids, writing_briefs=True
        )

    return collect


async def test_interval_citations_memory_and_frozen_read(
    collect_stable_target, tmp_path, monkeypatch
):
    base = activity()[0]
    seen = []

    async def send(stage, payload, schema):
        seen.append((stage, deepcopy(payload), deepcopy(schema)))
        flows = payload.get("journey_relations", [])
        if stage == "space" and flows:
            assert {f["id"] for f in flows} <= set(
                schema["properties"]["relation_ids"]["items"]["enum"]
            )
            return json.dumps(
                {
                    "text": "공원과 가까워졌다가 다시 거리가 벌어졌다.",
                    "focus": "구간 관계",
                    "evidence_ids": [flows[0]["id"]],
                    "relation_ids": [flows[0]["id"]],
                }
            )
        return answer(stage, payload)

    result = await write_relational_board(
        base.input.source,
        base,
        prepare=collect_stable_target,
        send=send,
        execution_policy=RelationalExecutionPolicy(minimum_interval_s=0),
    )
    flows = [
        f
        for stage, request, _ in seen
        if stage == "space"
        for f in request.get("journey_relations", [])
    ]
    assert flows
    assert all(r["status"] == "returned" for r in result.receipt["writing"]["results"])
    assert any(
        m.get("selected_journey_relations")
        for stage, request, _ in seen
        if stage == "space"
        for m in request.get("delivery_memory", [])
    )
    for stage, request, _ in seen:
        if stage == "action":
            assert not {"journey_relations", "earlier", "delivery_memory"} & request.keys()
            assert request["required_event"]["actor"]["entity_type"] == "dog"
    text = json.dumps(seen)
    assert all(
        key not in text
        for key in ("source_version", "source_bindings", "recording_device", "calculation_policy")
    )
    path = tmp_path / "interval.json"
    save_skeleton(path, {"prepared": result.prepared, "receipt": result.receipt})

    def forbidden(*args, **kwargs):
        raise AssertionError("GET must not recompute interval relationships")

    from daengs_walk.diary.relational import interval_materials, relation_injection

    monkeypatch.setattr(interval_materials, "attach_interval_materials", forbidden)
    monkeypatch.setattr(relation_injection, "select_relations", forbidden)
    assert read_skeleton(path) == result.receipt


async def test_route_differences_and_changed_target_are_not_static_background(
    collect_stable_target,
):
    straight = await collect_stable_target(activity(waypoints=[(0, 0, 0), (240, 0, 240)])[0])
    returning = await collect_stable_target(activity()[0])

    def cases(prepared):
        return {
            f["case"]
            for row in prepared["snapshot"]["frames"]
            for f in row["narrative_context"]["interval_relations"]["flows"]
        }

    assert "distance_valley" in cases(returning)
    assert cases(straight) != cases(returning)
    frames = returning["snapshot"]["frames"]
    source = IntervalSources.model_validate(returning["snapshot"]["interval_sources"])
    current, previous = deepcopy(frames[-1]), deepcopy(frames[-2])
    for fact in previous["scene_snapshot"]["facts"]:
        if fact["family"] == "surrounding_object":
            fact["value"]["registered_point"]["lat"] += 0.001
    context = SpaceWritingBrief(context=current["narrative_context"]).context
    actual = attach_interval_materials(context, source, current, previous)
    assert not any(f.target_id for f in actual.interval_relations.flows)


async def test_gap_and_unknown_accuracy_cannot_support_distance_or_return(collect_stable_target):
    prepared = await collect_stable_target(activity(gap=True)[0])
    frames = prepared["snapshot"]["frames"]
    sources = IntervalSources.model_validate(prepared["snapshot"]["interval_sources"])
    # Dropped canonical edges break continuity even if consecutive timestamps are close.
    broken = sources.model_copy(update={"edges": ()})
    unknown = sources.model_copy(
        update={"points": tuple(p.model_copy(update={"accuracy_m": None}) for p in sources.points)}
    )
    for source in (broken, unknown):
        for previous, frame in pairwise(frames):
            context = SpaceWritingBrief(context=frame["narrative_context"]).context
            actual = attach_interval_materials(context, source, frame, previous)
            assert not any(
                f.target_id or f.case == "route_return" for f in actual.interval_relations.flows
            )


async def test_interval_cannot_bypass_source_or_citation_validation(collect_stable_target):
    prepared = await collect_stable_target(activity()[0])
    validate_prepared(prepared)
    frame = next(
        f
        for f in prepared["snapshot"]["frames"]
        if f["narrative_context"]["interval_relations"]["flows"]
    )
    brief = SpaceWritingBrief(context=frame["narrative_context"])
    with pytest.raises(ValueError, match="references"):
        resolve_brief_answer(
            brief,
            {
                "text": "문장",
                "focus": "관계",
                "evidence_ids": ["source:internal"],
                "relation_ids": [],
            },
        )
    frame["narrative_context"]["interval_relations"]["flows"][0]["target_name"] = "다른 공원"
    prepared["revision"] = digest(prepared["snapshot"])
    with pytest.raises(ValueError, match="source facts"):
        validate_prepared(prepared)


def test_route_only_opens_space_requests_without_leaking_into_current_action():
    prepared = prepare_relational_diary(activity()[0], writing_briefs=True)
    validate_prepared(prepared)
    selected = []
    for frame, plan in zip(
        prepared["snapshot"]["frames"], prepared["snapshot"]["plans"], strict=True
    ):
        context = SpaceWritingBrief(context=frame["narrative_context"])
        assert not context.context.current_facts
        flows = context.context.interval_relations.flows
        if flows:
            assert plan["space_task"] and plan["state_transition"] == "interval_context"
            selected.extend(flows)
        if plan["action_task"]:
            assert "interval_relations" not in json.dumps(plan["action_task"])
    assert {f.case for f in selected} >= {"route_retrace", "route_straight"}
    assert "route_turn" not in {f.case for f in selected}
    assert len({f.id for f in selected}) == len(selected)


@pytest.mark.parametrize("gap,expected", [(False, True), (True, False)])
def test_return_needs_a_complete_departure_and_return(gap, expected):
    base = activity(gap=gap, waypoints=[(0, 0, 0), (120, 0, 120), (240, 0, 0)])[0]
    prepared = prepare_relational_diary(base, writing_briefs=True)
    flows = [
        f
        for frame in prepared["snapshot"]["frames"]
        for f in SpaceWritingBrief(
            context=frame["narrative_context"]
        ).context.interval_relations.flows
    ]
    assert any(f.case == "route_return" for f in flows) is expected


async def test_replaced_endpoint_id_is_not_exposed_or_accepted(collect_stable_target):
    prepared = await collect_stable_target(activity(waypoints=[(0, 0, 0), (240, 0, 240)])[0])
    briefs = [
        SpaceWritingBrief(context=f["narrative_context"]) for f in prepared["snapshot"]["frames"]
    ]
    brief = next(b for b in briefs if b.context.interval_relations.replaced_relation_ids)
    replaced_ids = set(brief.context.interval_relations.replaced_relation_ids)
    view = writer_view(brief)
    assert not replaced_ids & set(view["relation_ids"])
    assert not replaced_ids & {
        r["id"] for rows in view.get("relation_slots", {}).values() for r in rows
    }
    with pytest.raises(ValueError, match="relation references"):
        resolve_brief_answer(
            brief,
            {
                "text": "문장",
                "focus": "관계",
                "evidence_ids": [brief.citation_ids[0]],
                "relation_ids": list(replaced_ids),
            },
        )
