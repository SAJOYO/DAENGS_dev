"""Real card orchestration and HTTP publication; only external providers are fakes."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from daengs_backend.routers import walk_storyboard as router
from daengs_backend.services import walk_diary_card_writing as writing
from daengs_backend.services import walk_diary_space_collection as collection
from daengs_backend.services.walk_diary_base_board import (
    assemble_saved_base_board,
    with_scene_backgrounds,
)
from daengs_backend.services.walk_diary_board_storage import load_board, store_board
from daengs_backend.services.walk_diary_deadline import publication_deadline
from daengs_backend.services.walk_diary_input import InputAssembly
from daengs_backend.services.walk_diary_observations import ObservationSource
from daengs_backend.services.walk_diary_prepare import PreparedWalkDiary
from daengs_walk.diary_input import DiaryInput, digest
from tests.walk.diary.test_diary_board_slot_writing import prepared_case
from tests.walk.diary.test_diary_route_patterns import input_case
from tests.walk.diary.test_diary_space_integration import public_response
from tests.walk.support.base_board import policy
from tests.walk.support.diary_generation import PATH, body


def prepared(action=True):
    source, route, _ = input_case("out_back", 30, pin=action)
    raw = source.model_dump(mode="json")
    raw["pet_ids"] = ["pet-bori"]
    if action:
        raw["records"][0]["content"]["pet_id"] = "pet-bori"
    source = DiaryInput.model_validate(raw)
    assembled = InputAssembly(
        source,
        (),
        ObservationSource(route.version, evidence=route.evidence),
        pet_names=(("pet-bori", "보리"),),
    )
    return assemble_saved_base_board(assembled, policy(3))


async def prose(stage, payload, schema):
    if stage == "title":
        return {
            "titles": [
                {
                    "card_id": c["card_id"],
                    "content_revision": c["content_revision"],
                    "text": "산책길에서 남긴 기록",
                }
                for c in reversed(payload["cards"])
            ]
        }
    result = {"card_id": payload["card_id"], "request_revision": payload["request_revision"]}
    if stage == "action":
        return {**result, "action_id": payload["action"]["id"], "text": "보리가 냄새를 맡았다."}
    refs = [m["id"] for m in payload["materials"]]
    return {**result, "text": "이 부근에 길이 있다." if refs else "", "evidence_ids": refs[:1]}


@pytest.mark.parametrize("has_pin", [False, True])
async def test_real_jobs_are_conditional_and_titles_see_only_frozen_bodies(has_pin):
    base = prepared(has_pin)
    provider = AsyncMock(side_effect=prose)
    result = await writing.write_cards(base.input.source, base, generate=provider)
    calls = provider.call_args_list
    assert sum(c.args[0] == "space" for c in calls) == len(base.board.scenes)
    assert sum(c.args[0] == "action" for c in calls) == int(has_pin)
    assert calls[-1].args[0] == "title"
    for call in calls:
        stage, payload, _ = call.args
        if stage == "space":
            assert "action" not in payload and "original_text" not in payload
            assert set(payload["sources"]) == {"sgis", "egis", "environment"}
            assert all(m["role"] != "scene_route_pattern" for m in payload["materials"])
        if stage == "action":
            assert payload["action"]["actor"] == {"id": "pet-bori", "name": "보리"}
            assert not {"materials", "anchor", "place_reference", "space"} & payload.keys()
    assert all("보리" not in c.writing.space.text for c in result.bundle.scenes)
    assert sum(bool(c.writing.actions) for c in result.bundle.scenes) == int(has_pin)
    titles = calls[-1].args[1]["cards"]
    for card, sent in zip(result.bundle.scenes, titles, strict=True):
        assert sent["space"] == card.writing.space.model_dump(mode="json")
        assert sent["content_revision"] == card.writing.content_revision
        assert "original_text" not in sent


async def test_action_starts_while_space_collection_is_blocked():
    base = prepared()
    action_started = asyncio.Event()

    async def generate(stage, payload, schema):
        if stage == "action":
            action_started.set()
        return await prose(stage, payload, schema)

    async def collect(board):
        await asyncio.wait_for(action_started.wait(), 0.2)
        raise OSError("synthetic source outage")

    result = await writing.write_cards(
        base.input.source, base, generate=generate, collector=collect
    )
    assert action_started.is_set()
    assert any(
        c.writing.actions[0].origin == "generated"
        for c in result.bundle.scenes
        if c.writing.actions
    )


async def test_action_edit_does_not_change_space_request():
    base = prepared()
    before = next(s for s in base.board.scenes if s.core.kind == "user_record")
    index = base.board.scenes.index(before)
    raw = base.input.source.model_dump(mode="json")
    raw["records"][0]["content"]["code"] = "barking"
    raw["records"][0]["ref"]["version"] = "3"
    after = assemble_saved_base_board(
        replace(base.input, source=DiaryInput.model_validate(raw)), policy(3)
    )
    target = next(s for s in after.board.scenes if s.id == before.id)
    assert (
        writing.space_job(base, before, base.slots.stamps[index]).request
        == writing.space_job(
            after, target, after.slots.stamps[after.board.scenes.index(target)]
        ).request
    )
    assert (
        writing.action_job(base, before).request_revision
        != writing.action_job(after, target).request_revision
    )
    previous = await writing.write_cards(base.input.source, base, generate=prose)
    cached = replace(after, cached_jobs=tuple(j.model_dump(mode="json") for j in previous.jobs))
    provider = AsyncMock(side_effect=prose)
    await writing.write_cards(cached.input.source, cached, generate=provider)
    assert [c.args[0] for c in provider.call_args_list] == ["action", "title"]


async def test_note_is_preserved_and_not_sent_even_to_title():
    base = prepared_case().board
    provider = AsyncMock(side_effect=prose)
    result = await writing.write_cards(base.input.source, base, generate=provider)
    for original, card in zip(base.board.scenes, result.bundle.scenes, strict=True):
        if original.core.kind == "user_record":
            assert card.writing.original_text == original.body and card.body.endswith(original.body)
            assert all(
                original.body not in json.dumps(c.args[1], ensure_ascii=False)
                for c in provider.call_args_list
            )
    prepared_value = PreparedWalkDiary(base.input, base.plan.intermediate, base)
    stored = store_board(prepared_value, result.bundle, digest("test-generation"), writing=result)
    assert load_board(stored).bundle == result.bundle
    stored["writing_receipt"]["result"]["jobs"][0]["request"]["card_id"] = "changed"
    with pytest.raises(ValueError):
        load_board(stored)


async def test_more_than_twelve_cards_still_run_and_titles_are_batched():
    from copy import deepcopy

    base = prepared()
    raw = base.input.source.model_dump(mode="json")
    template = raw["records"][0]
    raw["records"] = []
    for index in range(13):
        record = deepcopy(template)
        record["ref"]["id"] = f"pin-{index}"
        raw["records"].append(record)
    base = assemble_saved_base_board(
        replace(base.input, source=DiaryInput.model_validate(raw)), policy(3)
    )
    provider = AsyncMock(side_effect=prose)
    result = await writing.write_cards(base.input.source, base, generate=provider)
    assert len(result.bundle.scenes) > 12
    titles = [c.args[1]["cards"] for c in provider.call_args_list if c.args[0] == "title"]
    assert len(titles) == 2 and all(len(batch) <= 12 for batch in titles)
    assert all(c.writing.title_origin == "generated" for c in result.bundle.scenes)


async def test_wrong_title_revision_keeps_adopted_action():
    base = prepared()

    async def generate(stage, payload, schema):
        value = await prose(stage, payload, schema)
        if stage == "title":
            value["titles"][0]["content_revision"] = digest("wrong-body")
        return value

    result = await writing.write_cards(base.input.source, base, generate=generate)
    assert all(c.writing.title_origin == "fallback" for c in result.bundle.scenes)
    assert any(
        c.writing.actions[0].origin == "generated"
        for c in result.bundle.scenes
        if c.writing.actions
    )


@pytest.mark.parametrize("failure", ["title", "action", "space"])
async def test_one_failed_strategy_keeps_other_adopted_parts(failure):
    base = prepared()

    async def generate(stage, payload, schema):
        if stage == failure:
            raise OSError("test provider failure")
        return await prose(stage, payload, schema)

    result = await writing.write_cards(base.input.source, base, generate=generate)
    action = next(c for c in result.bundle.scenes if c.writing.actions)
    assert action.writing.actions[0].origin == ("fallback" if failure == "action" else "generated")
    assert action.writing.title_origin == ("fallback" if failure == "title" else "generated")
    assert any(j.failure_code == "provider_failed" for j in result.jobs)


async def test_expired_action_cannot_replace_adopted_body_after_publication(monkeypatch):
    base = prepared()
    release = asyncio.Event()

    async def generate(stage, payload, schema):
        if stage == "action":
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
        return await prose(stage, payload, schema)

    token = publication_deadline.set(datetime.now(UTC) + timedelta(seconds=0.45))
    try:
        result = await writing.write_cards(base.input.source, base, generate=generate)
        before = result.model_dump(mode="json")
        release.set()
        await asyncio.sleep(0)
        assert result.model_dump(mode="json") == before
        assert any(j.failure_code == "budget_exceeded" for j in result.jobs)
    finally:
        release.set()
        publication_deadline.reset(token)


def sgis_response(request):
    if "authentication" in request.url.path:
        return httpx.Response(
            200,
            json={"errCd": 0, "result": {"accessToken": "test-token", "accessTimeout": 9999999999}},
        )
    if "transcoord" in request.url.path:
        return httpx.Response(200, json={"errCd": 0, "result": {"posX": 950000, "posY": 1940000}})
    if "rgeocode" in request.url.path:
        assert request.url.params["addr_type"] == "20"
        return httpx.Response(
            200,
            json={
                "errCd": 0,
                "result": [
                    {
                        "sido_nm": "서울특별시",
                        "sgg_nm": "강남구",
                        "emdong_nm": "도곡1동",
                        "sido_cd": "11",
                        "sgg_cd": "680",
                        "emdong_cd": "655",
                    }
                ],
            },
        )
    return public_response(request)


async def collect_with_sgis(board):
    return await collection.collect_spaces(
        board,
        transport=httpx.MockTransport(sgis_response),
        include_sgis=True,
        sgis_key="test-only",
        sgis_secret="test-only",
    )


async def test_sgis_and_egis_follow_actual_normalizers_into_request_and_card(monkeypatch):
    from daengs_backend.services.walk_sgis import SgisSource

    monkeypatch.setattr(collection, "sgis", SgisSource())
    base = prepared()
    result = await writing.write_cards(
        base.input.source, base, generate=prose, collector=collect_with_sgis
    )
    assert result.scene_backgrounds is not None
    for call in result.jobs:
        if call.stage == "space":
            assert call.request["sources"]["sgis"] == "known"
            assert call.request["sources"]["egis"] == "known"
            assert call.request["scene_structure"]["current_ground"]
    for card in result.bundle.scenes:
        assert card.place_reference[0].facts["dong"] == "도곡1동"
        assert card.place_reference[0].facts["sido"] == "서울특별시"
    bound = with_scene_backgrounds(base, result.scene_backgrounds)
    prepared_value = PreparedWalkDiary(base.input, base.plan.intermediate, bound)
    stored = store_board(
        prepared_value, result.bundle, digest("collected-generation"), writing=result
    )
    assert load_board(stored).bundle == result.bundle


def test_actual_http_writer_publishes_once_and_exports_app_contract(api, monkeypatch):
    from copy import deepcopy
    from uuid import UUID

    from daengs_backend.services import walk_diary_input as reader

    client, state, db = api
    pet_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    state.walk.pet_ids = [pet_id]
    monkeypatch.setattr(reader.pets, "accessible_ids", AsyncMock(return_value={pet_id}))
    monkeypatch.setattr(reader.pets, "names_by_ids", AsyncMock(return_value={pet_id: "보리"}))
    pin = deepcopy(state.entries[0])
    pin.id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    pin.payload = {
        **pin.payload,
        "kind": "behavior",
        "behavior_code": "sniffing",
        "pet_id": str(pet_id),
    }
    pin.payload.pop("note")
    state.entries.append(pin)
    state.envelope = None
    client.app.dependency_overrides.pop(router.get_diary_writer)
    monkeypatch.setattr(writing, "generate_card_prose", AsyncMock(side_effect=prose))

    async def collector(board):
        assert state.row.status == "running" and db.commit.await_count >= 1
        return await collect_with_sgis(board)

    spy = AsyncMock(side_effect=collector)
    monkeypatch.setattr(collection, "configured_collection", spy)
    request = body(state, bundle_format="walk-diary-board-v1", preparation_budget_ms=20000)
    response = client.post(PATH, json=request)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["status"] == "ready", value
    assert value["bundle"]["model_status"] == "accepted", state.row.bundle
    assert all(s["writing"]["title_origin"] == "generated" for s in value["bundle"]["scenes"])
    assert sum(bool(s["writing"]["actions"]) for s in value["bundle"]["scenes"]) == 1
    assert (
        client.get(PATH + "?bundle_format=walk-diary-board-v1&target_scene_count=3").json() == value
    )
    assert client.post(PATH, json=request).json() == value
    spy.assert_awaited_once()
    # Export opt-in, never silently rewrite the shared fixture during normal test runs.
    import os

    target = os.environ.get("DAENGS_CARD_FIXTURE_OUT")
    if target:
        Path(target).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
