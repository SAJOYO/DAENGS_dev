"""Native tool turns over saved evidence; Gemini and public APIs are never called."""

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from google.genai import types
from pydantic import SecretStr

from daengs_backend.services.walk_diary import runtime, space_details
from daengs_backend.services.walk_diary.deadline import publication_deadline
from daengs_backend.services.walk_diary.model_input import SpaceAnswer
from daengs_backend.services.walk_diary.preparation.board import with_scene_backgrounds
from daengs_backend.services.walk_diary.preparation.diary import PreparedWalkDiary
from daengs_backend.services.walk_diary.storage.board import load_board, store_board
from daengs_backend.services.walk_diary.writing.provider import generate_card_prose
from daengs_backend.services.walk_diary.writing.space_dialogue import write_space
from daengs_walk.diary.board.backgrounds import SceneBackgroundSnapshot
from daengs_walk.diary.contracts.input import digest
from tests.walk.diary.test_diary_card_writing import prose
from tests.walk.support.paths import REPO
from tools.run_diary_route_scenario import prepare, read


def payload():
    path = REPO / "backend/evals/diary_route_scenario/context-lookup-01/contexts.json"
    return json.loads(path.read_text(encoding="utf-8"))[0]["space"]


def response(*, text=None, calls=()):
    parts = [
        types.Part(
            function_call=types.FunctionCall(id=f"call-{i}", name=name, args=args),
            thought_signature=b"opaque-signature",
        )
        for i, (name, args) in enumerate(calls)
    ]
    if text is not None:
        parts.append(types.Part(text=json.dumps(text, ensure_ascii=False)))
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=parts))]
    )


def selected(seed):
    return space_details.initial_input(seed)["available_details"][0]["id"]


def tool_response(seed):
    return response(calls=[(space_details.NAME, {"material_ids": [selected(seed)]})])


def final_response(refs):
    return response(text={"text": "주변 배경을 남겼다." if refs else "", "evidence_ids": refs})


async def test_optional_lookup_supplies_only_selected_details_and_keeps_native_signature():
    seed = payload()
    original = deepcopy(seed)
    first = tool_response(seed)
    send = AsyncMock(side_effect=[first, final_response([selected(seed)])])
    result = await write_space(seed, send)
    assert result.failure_code is None
    assert seed == original
    initial = json.loads(send.call_args_list[0].args[0][0].parts[0].text)
    assert all(m["role"] in space_details.CORE_ROLES for m in initial["materials"])
    assert all(set(c) == {"id", "topic"} for c in initial["available_details"])
    assert send.call_args_list[0].args[1][0]["name"] == space_details.NAME
    assert send.call_args_list[1].args[1] == []  # no second lookup opportunity
    history = send.call_args_list[1].args[0]
    assert history[1] is first.candidates[0].content
    assert history[1].parts[0].thought_signature == b"opaque-signature"
    reply = history[2].parts[0].function_response
    assert reply.id == first.candidates[0].content.parts[0].function_call.id
    assert [m["id"] for m in reply.response["materials"]] == [selected(seed)]
    assert result.trace["model_calls"] == 2 and result.trace["public_api_calls"] == 0
    assert "opaque-signature" not in json.dumps(result.trace)


@pytest.mark.parametrize("details", [True, False])
async def test_basic_material_can_finish_without_a_tool_call(details):
    seed = payload()
    if not details:
        seed = {"materials": space_details.initial_input(seed)["materials"]}
    ref = space_details.initial_input(seed)["materials"][0]["id"]
    send = AsyncMock(return_value=final_response([ref]))
    result = await write_space(seed, send)
    assert result.value["evidence_ids"] == [ref]
    assert result.trace["model_calls"] == 1 and result.trace["tool_calls"] == []
    assert bool(send.call_args.args[1]) == details


@pytest.mark.parametrize(
    "arguments",
    [
        {"material_ids": ["another-card"]},
        {"material_ids": ["m2"], "card_id": "another-card"},
        {"material_ids": ["m2"], "lat": 37},
        {"material_ids": ["m2", "m2"]},
        {"material_ids": ["m1", "m2", "m3"]},
        {"material_ids": "m2"},
    ],
)
async def test_invalid_scope_or_arguments_return_no_facts(arguments):
    send = AsyncMock(
        side_effect=[response(calls=[(space_details.NAME, arguments)]), final_response([])]
    )
    result = await write_space(payload(), send)
    assert result.failure_code is None
    assert result.trace["tool_calls"] == [
        {"name": space_details.NAME, "arguments": None, "result": {"status": "invalid_arguments"}}
    ]


async def test_unread_candidate_is_not_citable():
    seed = payload()
    send = AsyncMock(return_value=final_response([selected(seed)]))
    result = await write_space(seed, send)
    assert result.failure_code == "invalid_response" and result.value is None
    assert send.await_count == 1


@pytest.mark.parametrize("case", ["wrong_tool", "multiple", "repeat"])
async def test_unavailable_or_repeated_tools_cannot_expand_the_loop(case):
    seed = payload()
    valid = (space_details.NAME, {"material_ids": [selected(seed)]})
    responses = (
        [response(calls=[("get_action_context", {})])]
        if case == "wrong_tool"
        else [response(calls=[valid, valid])]
        if case == "multiple"
        else [response(calls=[valid]), response(calls=[valid])]
    )
    send = AsyncMock(side_effect=responses)
    result = await write_space(seed, send)
    assert result.failure_code == "invalid_response"
    assert send.await_count == (2 if case == "repeat" else 1)


async def test_cancellation_suppression_cannot_start_a_followup_request():
    entered = asyncio.Event()

    async def suppressed(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return tool_response(payload())

    send = AsyncMock(side_effect=suppressed)
    task = asyncio.create_task(write_space(payload(), send))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert send.await_count == 1


@pytest.mark.parametrize("stage", ["space", "action", "title"])
async def test_default_provider_registers_native_tool_only_for_space(monkeypatch, stage):
    from google import genai

    from daengs_backend.config import settings

    monkeypatch.setattr(settings, "gemini_api_key", SecretStr("not-a-real-key"))
    seed = payload()
    responses = (
        [tool_response(seed), final_response([selected(seed)])]
        if stage == "space"
        else [final_response([])]
    )
    call = AsyncMock(side_effect=responses)
    client = SimpleNamespace(models=SimpleNamespace(generate_content=call))
    context = AsyncMock()
    context.__aenter__.return_value = client
    monkeypatch.setattr(genai, "Client", lambda **kwargs: SimpleNamespace(aio=context))
    result = await generate_card_prose(stage, seed, SpaceAnswer.model_json_schema())
    config = call.call_args_list[0].kwargs["config"]
    assert config.automatic_function_calling.disable is True
    if stage == "space":
        assert result.failure_code is None and call.await_count == 2
        assert config.tools[0].function_declarations[0].name == space_details.NAME
        assert config.tool_config.function_calling_config.mode == "AUTO"
        assert config.response_json_schema is None
        assert call.call_args_list[1].kwargs["config"].tools is None
        assert call.call_args_list[1].kwargs["config"].response_json_schema is not None
    else:
        assert call.await_count == 1 and config.tools is None
        assert isinstance(result, str)


def public_base():
    source = REPO / "backend/evals/diary_route_scenario/public-02"
    return with_scene_backgrounds(
        prepare(read(source / "input.json")),
        SceneBackgroundSnapshot.model_validate(read(source / "backgrounds.json")),
    )


async def generated(stage, seed, schema):
    if stage != "space":
        return await prose(stage, seed, schema)
    if space_details.declaration(seed):
        send = AsyncMock(side_effect=[tool_response(seed), final_response([selected(seed)])])
    else:
        send = AsyncMock(return_value=final_response([]))
    return await write_space(seed, send)


async def test_real_graph_storage_and_cache_keep_the_tool_receipt():
    base = public_base()
    result = await runtime.write_cards(base.input.source, base, generate=generated)
    assert all(j.failure_code is None for j in result.jobs)
    assert sum(j.stage == "action" for j in result.jobs) == 1
    spaces = [j for j in result.jobs if j.stage == "space"]
    assert all(j.tool_trace["model_calls"] == 2 for j in spaces)
    assert all(j.tool_trace is None for j in result.jobs if j.stage != "space")
    stored = store_board(
        PreparedWalkDiary(base.input, base.plan.intermediate, base),
        result.bundle,
        digest(["space-tool-test"]),
        writing=result,
    )
    loaded = load_board(stored)
    assert loaded.model_dump(mode="json") == stored
    trace = loaded.writing_receipt.result.jobs[0].tool_trace
    assert trace is not None
    damaged = deepcopy(loaded.writing_receipt.model_dump(mode="json"))
    trace = damaged["result"]["jobs"][0]["tool_trace"]
    trace["tool_calls"][0]["result"]["materials"][0]["relation"] = "공원 안에 있음"
    with pytest.raises(ValueError, match="space tool result changed"):
        type(loaded.writing_receipt).model_validate(damaged)
    cached = replace(base, cached_jobs=tuple(j.model_dump(mode="json") for j in result.jobs))
    call = AsyncMock(side_effect=generated)
    again = await runtime.write_cards(cached.input.source, cached, generate=call)
    call.assert_not_awaited()
    assert [j.tool_trace for j in again.jobs] == [j.tool_trace for j in result.jobs]


async def test_late_tool_completion_cannot_change_frozen_card_or_titles():
    base = public_base()
    entered, release = asyncio.Event(), asyncio.Event()

    async def late(stage, seed, schema):
        if stage != "space":
            return await prose(stage, seed, schema)

        async def send(contents, tools):
            if tools:
                return tool_response(seed)
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return final_response([selected(seed)])

        return await write_space(seed, send)

    token = publication_deadline.set(datetime.now(UTC) + timedelta(seconds=0.8))
    try:
        result = await runtime.write_cards(base.input.source, base, generate=late)
        assert entered.is_set()
        before = result.model_dump(mode="json")
        assert all(j.accepted is None for j in result.jobs if j.stage == "space")
        release.set()
        await asyncio.sleep(0)
        assert result.model_dump(mode="json") == before
    finally:
        release.set()
        publication_deadline.reset(token)
