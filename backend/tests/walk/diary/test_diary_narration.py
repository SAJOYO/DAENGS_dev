"""Shared narrator context at the actual wire, tool and historical receipt boundaries."""

import json
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from daengs_backend.services.walk_diary import space_details
from daengs_backend.services.walk_diary.model_input import normalize
from daengs_backend.services.walk_diary.writing import jobs, policy
from daengs_backend.services.walk_diary.writing.context import (
    get_action_context,
    get_space_context,
)
from daengs_backend.services.walk_diary.writing.space_dialogue import write_space
from daengs_walk.diary.board.activity import require_activity_transfer
from daengs_walk.diary.board.narration import VERSION, narration_context
from tests.walk.diary.test_diary_space_tools import (
    final_response,
    payload,
    public_base,
    selected,
    tool_response,
)
from tests.walk.support.paths import REPO


def test_space_and_action_share_viewpoint_without_changing_the_materials():
    base = public_base()
    scene = next(s for s in base.board.scenes if get_action_context(base, s.id) is not None)
    contexts = [get_space_context(base, scene.id), get_action_context(base, scene.id)]
    expected = {
        "narrator": "이 산책을 기록한 보호자(나)",
        "companions": [{"name": "보리"}],
        "scope": "현재 장면",
    }
    for context in contexts:
        wire = normalize(context.stage, context.request)
        assert wire.payload["narration"] == expected
        legacy_request = deepcopy(context.request)
        legacy_request["walk_context"].pop("narration_version")
        legacy = normalize(context.stage, legacy_request)
        assert {k: v for k, v in wire.payload.items() if k != "narration"} == legacy.payload
        assert wire.references == legacy.references
        if context.stage == "action":
            require_activity_transfer(context.request, wire.payload, wire.references)
            damaged = deepcopy(wire.payload)
            damaged["narration"]["companions"] = [{"name": "다른 동행"}]
            with pytest.raises(ValueError):
                require_activity_transfer(context.request, damaged, wire.references)
    no_pin = next(s for s in base.board.scenes if get_action_context(base, s.id) is None)
    assert get_space_context(base, no_pin.id).llm_input["narration"] == expected
    assert get_action_context(base, no_pin.id) is None


def test_names_are_optional_and_internal_metadata_is_not_a_writing_material():
    value = narration_context(
        {
            "narration_version": VERSION,
            "record_kind": "guardian_walk_diary",
            "companions": [
                {"id": "one", "name": " 두리 ", "medical_note": "hidden"},
                {"id": "two", "name": None},
            ],
            "original_text": "private memo",
            "lat": 37,
        }
    )
    assert value["companions"] == [{"name": "두리"}, {"name": None}]
    assert set(value) == {"narrator", "companions", "scope"}
    assert not any(t in json.dumps(value) for t in ("hidden", "private", "one", "two"))
    with pytest.raises(ValueError):
        narration_context({"narration_version": "unknown"})


async def test_both_native_rounds_and_receipt_preserve_the_same_narration():
    seed = payload()
    base = public_base()
    seed["narration"] = get_space_context(base, base.board.scenes[0].id).llm_input["narration"]
    send = AsyncMock(side_effect=[tool_response(seed), final_response([selected(seed)])])
    result = await write_space(seed, send)
    assert result.failure_code is None
    for call in send.call_args_list:
        actual = json.loads(call.args[0][0].parts[0].text)
        assert actual["narration"] == seed["narration"]
    assert result.trace["initial_input"]["narration"] == seed["narration"]
    space_details.validate_trace(seed, result.trace)
    damaged = deepcopy(result.trace)
    damaged["initial_input"]["narration"]["companions"] = []
    with pytest.raises(ValueError):
        space_details.validate_trace(seed, damaged)
    damaged = deepcopy(result.trace)
    damaged["version"] = space_details.LEGACY_VERSION
    damaged["initial_input"].pop("narration")
    with pytest.raises(ValueError):
        space_details.validate_trace(seed, damaged)


def test_companion_presence_is_allowed_only_for_the_new_space_contract():
    base = public_base()
    context = get_space_context(base, base.board.scenes[0].id)
    for legacy in (False, True):
        request = deepcopy(context.request)
        if legacy:
            request["walk_context"].pop("narration_version")
        job = jobs.job("space", request)
        wire = normalize("space", job.request)
        answer = {"text": "보리와 함께한 산책의 배경을 남겼다.", "evidence_ids": ["m1"]}
        result = jobs.validate_output(job, wire.restore(answer))
        assert bool(result.accepted) is (not legacy)
        # Narration itself cannot substitute for a spatial citation.
        empty = jobs.validate_output(
            job, wire.restore({"text": "보리와 산책했다.", "evidence_ids": []})
        )
        assert empty.failure_code == "invalid_response"


def test_saved_v1_tool_receipts_and_title_strategy_stay_unchanged():
    root = REPO / "backend/evals/diary_route_scenario/space-tools-gemini-01"
    previous = json.loads((root / "experiment.json").read_text(encoding="utf-8"))
    for case in previous["cases"]:
        result = json.loads(
            (root / f"case-{case['index']}-optional_details.json").read_text(encoding="utf-8")
        )
        assert result["tool_trace"]["version"] == space_details.LEGACY_VERSION
        assert "narration" not in case["payload"]
        space_details.validate_trace(case["payload"], result["tool_trace"])
    before = previous["writing_policy"]["prompts"]
    after = policy.writing_version()["prompts"]
    assert before["title"] == after["title"]
    assert before["space"] != after["space"]
    assert before["action"] != after["action"]
