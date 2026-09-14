"""Space interpretation, provider projection and stored-version compatibility; no network."""

import json
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from daengs_backend.services.walk_diary import space_details
from daengs_backend.services.walk_diary.model_input import normalize
from daengs_backend.services.walk_diary.writing import jobs, policy
from daengs_backend.services.walk_diary.writing.context import get_space_context
from daengs_backend.services.walk_diary.writing.space_dialogue import write_space
from daengs_walk.diary.board.space_scene import compile_scene, project_scene
from tests.walk.diary.test_diary_space_tools import (
    final_response,
    public_base,
    selected,
    tool_response,
)
from tests.walk.support.paths import REPO
from tools.run_diary_route_scenario import read


def materials():
    return [
        {"id": "address", "role": "scene_address_reference", "facts": {"dong": "양재1동"}},
        {
            "id": "forest",
            "role": "scene_geometry_distance",
            "facts": {
                "material": {"피복": "숲"},
                "relation": {"kind": "land_cover_at_query_point"},
            },
        },
        {
            "id": "park",
            "role": "scene_registered_point_distance",
            "facts": {
                "material": {"배경": "공원"},
                "relation": {"kind": "registered_park_point_distance", "distance_m": 70},
            },
        },
        {
            "id": "shops",
            "role": "scene_area_context",
            "facts": {
                "relation": {"kind": "registered_distribution_in_query_circle", "radius_m": 1000},
            },
        },
        {"id": "weather", "role": "grid_temperature_observation", "facts": {"temperature_c": 24}},
    ]


def test_shared_record_point_does_not_make_park_part_of_forest():
    facts = materials()
    before = deepcopy(facts)
    scene = compile_scene(facts)
    roles = {b["material_id"]: b for b in scene["bindings"]}
    assert scene["background"]["basis_ids"] == ["forest"]
    assert roles["park"] == {
        "material_id": "park",
        "purpose": "independent_surrounding",
        "subject": "record_location",
        "relation": "registered_point_proximity",
        "scope": "near_query_point",
        "background_link": "unconfirmed",
    }
    assert roles["shops"]["subject"] == "query_area"
    assert roles["shops"]["scope"] == "whole_query_area"
    assert roles["address"]["purpose"] == "location_context"
    assert roles["weather"]["scope"] == "regional_grid"
    assert all(b["subject"] != "forest" for b in scene["bindings"])
    assert scene == compile_scene(list(reversed(facts)))
    assert facts == before


def test_only_location_is_not_promoted_when_background_is_missing():
    facts = materials()[:1]
    scene = compile_scene(facts)
    assert scene["background"]["basis_ids"] == []
    item = jobs.job(
        "space",
        {
            "card_id": "c1",
            "materials": facts,
            "space_scene": scene,
            "walk_context": {"companions": []},
        },
    )
    model = normalize("space", item.request)
    invalid = jobs.validate_output(
        item, model.restore({"text": "산책하던 곳은 양재1동이었다.", "evidence_ids": ["m1"]})
    )
    assert invalid.failure_code == "invalid_response"
    empty = jobs.validate_output(item, model.restore({"text": "", "evidence_ids": []}))
    assert empty.failure_code is None and empty.accepted["text"] == ""


@pytest.mark.parametrize("damage", ["role", "scope", "basis", "facts"])
def test_frozen_meaning_cannot_be_changed_independently_from_facts(damage):
    facts = materials()
    scene = compile_scene(facts)
    if damage == "role":
        scene["bindings"][2]["purpose"] = "background_basis"
    elif damage == "scope":
        scene["bindings"][2]["subject"] = "forest"
    elif damage == "basis":
        scene["background"]["basis_ids"] = ["address"]
    else:
        facts[1]["facts"]["material"]["피복"] = "물길"
    with pytest.raises(ValueError, match="space scene differs"):
        project_scene(facts, scene, {m["id"]: f"m{i}" for i, m in enumerate(facts)})


def test_unsupported_or_contradictory_relation_is_not_guessed():
    facts = materials()
    facts[2]["facts"]["relation"]["kind"] = "park_inside_forest"
    with pytest.raises(ValueError, match="unsupported"):
        compile_scene(facts)
    facts[2]["facts"]["relation"]["kind"] = "land_cover_at_query_point"
    with pytest.raises(ValueError, match="disagree"):
        compile_scene(facts)


async def test_actual_tool_rounds_preserve_roles_scope_and_selected_fragment():
    base = public_base()
    context = get_space_context(base, base.board.scenes[2].id)
    seed = context.llm_input
    initial = space_details.initial_input(seed)
    candidate = next(c for c in initial["available_details"] if c["topic"] == "주변 공원")
    assert candidate["purpose"] == "independent_surrounding"
    assert candidate["subject"] == "record_location"
    assert candidate["background_link"] == "unconfirmed"
    initial_ids = {m["id"] for m in initial["materials"]}
    assert {b["material_id"] for b in initial["space_scene"]["bindings"]} == initial_ids
    assert candidate["id"] not in initial_ids
    send = AsyncMock(side_effect=[tool_response(seed), final_response([selected(seed)])])
    result = await write_space(seed, send)
    assert result.failure_code is None and result.trace["version"] == space_details.VERSION
    history = send.call_args_list[1].args[0]
    assert json.loads(history[0].parts[0].text) == initial
    reply = history[2].parts[0].function_response.response
    assert {b["material_id"] for b in reply["space_scene"]["bindings"]} == {selected(seed)}
    expected = next(
        b for b in seed["space_scene"]["bindings"] if b["material_id"] == selected(seed)
    )
    assert reply["space_scene"]["bindings"] == [expected]
    changed = deepcopy(result.trace)
    changed["tool_calls"][0]["result"]["space_scene"]["bindings"][0]["subject"] = "forest"
    with pytest.raises(ValueError, match="space tool result changed"):
        space_details.validate_trace(seed, changed)
    changed = deepcopy(result.trace)
    changed["version"] = space_details.NARRATION_VERSION
    with pytest.raises(ValueError):
        space_details.validate_trace(seed, changed)


async def test_tool_cannot_finish_with_only_location_even_when_it_is_seen():
    facts = materials()[:1]
    seed = normalize("space", {"materials": facts, "space_scene": compile_scene(facts)}).payload
    send = AsyncMock(return_value=final_response(["m1"]))
    result = await write_space(seed, send)
    assert result.failure_code == "invalid_response" and send.await_count == 1


def test_new_projection_keeps_materials_and_title_strategy_with_updated_body_prompts():
    base = public_base()
    context = get_space_context(base, base.board.scenes[0].id)
    request = deepcopy(context.request)
    request.pop("space_scene")
    before = normalize("space", request).payload
    after = context.llm_input
    assert {k: v for k, v in after.items() if k != "space_scene"} == before
    assert set(after["space_scene"]) == {"background", "bindings"}
    assert "material:" not in json.dumps(after)
    assert "policy_revision" not in json.dumps(after)
    root = REPO / "backend/evals/diary_route_scenario/narration-gemini-01"
    previous = read(root / "input.json")["writer"]["prompts"]
    current = policy.writing_version()["prompts"]
    assert previous["action"] != current["action"]  # Authored examples were removed.
    assert previous["title"] == current["title"]
    assert previous["space"] != current["space"]
    for index in (1, 2, 3):
        saved = read(root / f"case-{index}-space.json")["job"]
        assert normalize("space", saved["request"]).payload == saved["llm_request"]
        space_details.validate_trace(saved["llm_request"], saved["tool_trace"])
