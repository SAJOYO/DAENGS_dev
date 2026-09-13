"""Production slot prose, citation binding, and the existing publication deadline."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from daengs_backend.routers import walk_storyboard as router
from daengs_backend.schemas.walk_storyboard import StoryboardRequest
from daengs_backend.services import walk_diary_slot_writing as writer
from daengs_backend.services.walk_diary_base_board import assemble_saved_base_board
from daengs_backend.services.walk_diary_board_slot_writing import complete_slot_board, write_board
from daengs_backend.services.walk_diary_generation import generate_diary
from daengs_backend.services.walk_diary_input import InputAssembly
from daengs_backend.services.walk_diary_observations import ObservationSource
from daengs_backend.services.walk_diary_prepare import PreparedWalkDiary
from daengs_backend.services.walk_diary_publication import within_budget
from daengs_evals.diary_slots_demo import demo_input
from daengs_walk.diary_board_output import BOARD_FORMAT
from daengs_walk.diary_scene_input import scene_materials
from daengs_walk.diary_slots import SlotPolicy
from tests.walk.support.base_board import policy
from tests.walk.support.diary_generation import PATH, body
from tests.walk.support.photo_input import OWNER, WALK


def prepared_case(**slots):
    source, route, _ = demo_input()
    assembled = InputAssembly(source, (), ObservationSource(route.version, evidence=route.evidence))
    base = assemble_saved_base_board(assembled, policy(3), slot_policy=SlotPolicy(**slots))
    return PreparedWalkDiary(assembled, base.plan.intermediate, base)


def prose(payload):
    return {
        "scenes": [
            {
                "scene_id": s["scene_id"],
                "text": "주변에 공원이 있었고, 이동 속도는 다른 구간보다 느렸다.",
                "evidence_ids": [e["id"] for e in scene_materials(s)],
                "action_id": s["action"]["id"] if s["action"] else None,
            }
            for s in payload["scenes"]
        ]
    }


async def test_scene_parts_reach_writer_and_core_is_preserved():
    prepared = prepared_case()
    base = prepared.board
    before = base.slots.model_dump(mode="json")
    provider = AsyncMock(side_effect=lambda payload, schema: prose(payload))
    output = await write_board(prepared.input.source, base, provider)
    published = complete_slot_board(prepared, output)
    payload = provider.call_args.args[0]
    assert published.model_status == "accepted"
    assert len(payload["scenes"]) == 3
    for scene in payload["scenes"]:
        assert set(scene["scene"]) == {"where", "environment", "route_pattern"}
        assert all(set(e) == {"id", "role", "facts"} for e in scene_materials(scene))
    assert len(published.scenes) == len(base.board.scenes) == 5
    for actual, original in zip(published.scenes, base.board.scenes, strict=True):
        assert (actual.id, actual.order, actual.anchor, actual.core, actual.title) == (
            original.id,
            original.order,
            original.anchor,
            original.core_ref,
            original.title,
        )
        if original.core.kind == "user_record" and original.core.record.content.kind == "note":
            assert actual.body.endswith(original.body)
        elif any(s["scene_id"] == actual.id for s in payload["scenes"]):
            assert actual.body == "주변에 공원이 있었고, 이동 속도는 다른 구간보다 느렸다."
    assert base.slots.model_dump(mode="json") == before
    provider.assert_awaited_once()


@pytest.mark.parametrize("change", ["scene", "citation", "original", "too_long"])
async def test_invalid_prose_preserves_every_original(change):
    prepared = prepared_case()
    base = prepared.board
    raw = prose(writer.slot_payload(base.board, base.slots))
    if change == "scene":
        raw["scenes"].pop()
    elif change == "citation":
        raw["scenes"][0]["evidence_ids"] = raw["scenes"][1]["evidence_ids"]
    elif change == "original":
        raw["scenes"][0]["original"] = "모델이 바꾼 원문"
    else:
        raw["scenes"][0]["text"] = "가" * 221
    output = await write_board(prepared.input.source, base, AsyncMock(return_value=raw))
    assert output.failure_code == "invalid_response"
    published = complete_slot_board(prepared, output)
    assert [s.body for s in published.scenes] == [s.body for s in base.board.scenes]


@pytest.mark.parametrize("field", ["slot_revision", "writer_version"])
async def test_completion_rejects_receipt_for_different_input_or_writer(field):
    prepared = prepared_case()
    output = await write_board(
        prepared.input.source, prepared.board, AsyncMock(side_effect=lambda p, s: prose(p))
    )
    with pytest.raises(ValueError, match="another snapshot"):
        complete_slot_board(prepared, output.model_copy(update={field: "f" * 64}))


async def test_empty_slots_and_input_budget_never_call_provider(monkeypatch):
    empty = prepared_case(total_slots=0, include_location_reference=False)
    provider = AsyncMock()
    output = await write_board(empty.input.source, empty.board, provider)
    assert output.model_status == "not_requested"
    assert complete_slot_board(empty, output).scenes
    full = prepared_case()
    monkeypatch.setattr(writer, "MAX_INPUT_BYTES", 1)
    output = await write_board(full.input.source, full.board, provider)
    assert output.failure_code == "budget_exceeded"
    provider.assert_not_awaited()


async def test_timeout_and_external_cancel_have_distinct_results(monkeypatch):
    prepared = prepared_case()
    monkeypatch.setattr(writer, "TIMEOUT_SECONDS", 0.01)

    async def slow(*_):
        await asyncio.Event().wait()

    output = await write_board(prepared.input.source, prepared.board, slow)
    assert output.failure_code == "budget_exceeded"
    with pytest.raises(asyncio.CancelledError):
        await write_board(
            prepared.input.source, prepared.board, AsyncMock(side_effect=asyncio.CancelledError())
        )


def test_real_router_selects_slot_writer_without_changing_request_body(api, monkeypatch):
    client, state, _ = api
    client.app.dependency_overrides.pop(router.get_diary_writer)
    monkeypatch.setattr(router, "write_board", state.writer)
    response = client.post(PATH, json=body(state, bundle_format=BOARD_FORMAT))
    assert response.status_code == 200, response.text
    assert response.json()["bundle"]["model_status"] == "accepted"
    assert "scene" in state.provider.call_args.args[0]["scenes"][0]
    state.provider.assert_awaited_once()


async def test_deadline_publishes_base_even_if_slot_provider_ignores_cancel(api):
    _, state, db = api
    release, late = asyncio.Event(), asyncio.Event()

    async def provider(payload, schema):
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        late.set()
        return prose(payload)

    async def bounded(source, base):
        # Exercise the real slot writer inside the existing outer deadline.
        return await within_budget(
            lambda s, b: write_board(s, b, provider),
            source,
            base,
            datetime.now(UTC) + timedelta(milliseconds=20),
        )

    try:
        result = await asyncio.wait_for(
            generate_diary(
                db,
                OWNER,
                WALK,
                StoryboardRequest.model_validate(
                    body(state, bundle_format=BOARD_FORMAT, preparation_budget_ms=10000)
                ),
                writer=bounded,
            ),
            timeout=2,
        )
        assert result.status == "ready" and result.bundle.failure_code == "budget_exceeded"
        saved = state.row.bundle.copy()
        release.set()
        await asyncio.wait_for(late.wait(), 1)
        assert state.row.bundle == saved
    finally:
        release.set()


async def test_slot_sdk_is_one_structured_call_and_closes(monkeypatch):
    from google import genai

    generate = AsyncMock(return_value=SimpleNamespace(text="{}"))
    client = AsyncMock()
    client.__aenter__.return_value = SimpleNamespace(
        models=SimpleNamespace(generate_content=generate)
    )
    captured = []

    def construct(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(aio=client)

    monkeypatch.setattr(genai, "Client", construct)
    assert await writer.generate_slot_prose({}, {"type": "object"}, api_key="synthetic-key") == "{}"
    assert captured[0]["http_options"].retry_options.attempts == 1
    assert generate.call_args.kwargs["config"].response_json_schema == {"type": "object"}
    generate.assert_awaited_once()
    client.__aexit__.assert_awaited_once()
