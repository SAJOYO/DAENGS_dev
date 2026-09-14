"""Observation meaning survives independent writing, reuse and publication."""

import json
import os
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from daengs_backend.routers import walk_storyboard as router
from daengs_backend.services import walk_diary_card_writing as writing
from daengs_backend.services import walk_diary_space_collection as collection
from daengs_backend.services.walk_diary_base_board import (
    assemble_saved_base_board,
    with_scene_backgrounds,
)
from daengs_backend.services.walk_diary_board_storage import load_board, store_board
from daengs_backend.services.walk_diary_card_receipt import StoredCardWriting
from daengs_backend.services.walk_diary_input import InputAssembly
from daengs_backend.services.walk_diary_prepare import PreparedWalkDiary
from daengs_walk.diary_board_output import PublishedBoard
from daengs_walk.diary_card_narrative import CURRENT_OBSERVATION_TEXT
from daengs_walk.diary_input import DiaryInput, digest
from tests.walk.diary.test_diary_card_writing import collect_with_sgis, prose
from tests.walk.support.base_board import policy
from tests.walk.support.diary import observation, source
from tests.walk.support.diary_generation import PATH, body


def prepared(kind="observed_dwell"):
    observed = observation().model_copy(update={"kind": kind})
    return assemble_saved_base_board(InputAssembly(source(observations=[observed]), ()), policy(1))


def observed_card(result):
    return next(c for c in result.bundle.scenes if c.observation)


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("observed_dwell", "이 구간에서는 동선이 한곳에 모였다."),
        (
            "observed_slow",
            "이 구간에서는 산책 중 다른 이동 구간보다 상대적으로 느린 이동이 관측됐다.",
        ),
        (
            "observed_fast",
            "이 구간에서는 산책 중 다른 이동 구간보다 상대적으로 빠른 이동이 관측됐다.",
        ),
    ],
)
@pytest.mark.parametrize("space_fails", [False, True])
async def test_confirmed_observation_survives_space_success_or_failure(kind, expected, space_fails):
    base = prepared(kind)

    async def generate(stage, payload, schema):
        if stage == "space" and space_fails:
            raise OSError("synthetic provider failure")
        return await prose(stage, payload, schema)

    provider = AsyncMock(side_effect=generate)
    result = await writing.write_cards(
        base.input.source, base, generate=provider, collector=collect_with_sgis
    )
    card = observed_card(result)
    assert card.body == expected + "\n" + card.writing.space.text
    assert card.writing.space.origin == ("fallback" if space_fails else "generated")
    assert card.writing.actions == () and card.writing.original_text is None
    assert card.writing.observation.core == card.core
    assert card.writing.observation.subject == "recording_device"
    assert card.writing.observation.action_meaning == "not_inferred"
    for call in provider.call_args_list:
        stage, payload, _ = call.args
        if stage == "space":
            assert "observation" not in payload
            assert expected not in str(payload)
        if stage == "title":
            sent = payload["context"][list(result.bundle.scenes).index(card)]
            assert sent["body"] == card.body
            assert expected in sent["body"] and "content_revision" not in sent
    assert not any(call.args[0] == "action" for call in provider.call_args_list)
    for other in result.bundle.scenes:
        if other.id != card.id:
            assert other.writing.observation is None
            assert "observation" not in other.writing.model_dump(mode="json")
    bound = with_scene_backgrounds(base, result.scene_backgrounds)
    prepared_value = PreparedWalkDiary(base.input, base.plan.intermediate, bound)
    writing.complete_cards(prepared_value, result)
    stored = store_board(
        prepared_value, result.bundle, digest("observation-generation"), writing=result
    )
    assert load_board(stored).bundle == result.bundle


@pytest.mark.parametrize("change", ["kind", "version"])
async def test_observation_change_reuses_space_and_refreshes_whole_title_context(change):
    base = prepared()
    before = await writing.write_cards(base.input.source, base, generate=prose)
    raw = base.input.source.model_dump(mode="json")
    raw["observations"][0][change] = (
        "observed_slow" if change == "kind" else digest("new-observation")
    )
    after = assemble_saved_base_board(InputAssembly(DiaryInput.model_validate(raw), ()), policy(1))
    after = replace(after, cached_jobs=tuple(j.model_dump(mode="json") for j in before.jobs))
    provider = AsyncMock(side_effect=prose)
    result = await writing.write_cards(after.input.source, after, generate=provider)
    old_card, new_card = observed_card(before), observed_card(result)
    assert old_card.id == new_card.id
    assert old_card.writing.content_revision != new_card.writing.content_revision
    assert [call.args[0] for call in provider.call_args_list] == ["title"]
    assert len(provider.call_args.args[1]["cards"]) == len(result.bundle.scenes)
    assert new_card.writing.observation.kind == raw["observations"][0]["kind"]


@pytest.mark.parametrize("legacy_note_hash", [False, True])
async def test_historical_card_without_observation_part_is_read_unchanged(legacy_note_hash):
    base = prepared()
    result = await writing.write_cards(base.input.source, base, generate=prose)
    raw = result.bundle.model_dump(mode="json")
    for card in raw["scenes"]:
        parts = card["writing"]
        parts["format"] = "diary-card-narrative-v1"
        parts.pop("observation", None)
        card["body"] = parts["space"]["text"]
        values = [
            card["id"],
            card["anchor"],
            card["place_reference"],
            parts["space"],
            parts["actions"],
        ]
        if legacy_note_hash:
            values.append(parts["original_text"])
        parts["content_revision"] = parts["title_based_on_content_revision"] = digest(values)
    assert PublishedBoard.model_validate(raw).model_dump(mode="json") == raw
    # Historical reading is allowed; a new completion must preserve the observation.
    with pytest.raises(ValueError, match="confirmed observation|adopted jobs|accepted jobs"):
        writing.complete_cards(
            PreparedWalkDiary(base.input, base.plan.intermediate, base),
            result.model_copy(update={"bundle": PublishedBoard.model_validate(raw)}),
        )


@pytest.mark.parametrize("damage", ["core", "kind", "meaning", "missing_body"])
async def test_observation_part_cannot_change_its_source_or_disappear_from_body(damage):
    base = prepared()
    result = await writing.write_cards(base.input.source, base, generate=prose)
    raw = result.bundle.model_dump(mode="json")
    card = next(c for c in raw["scenes"] if c["observation"])
    part = card["writing"]["observation"]
    if damage == "core":
        part["core"]["version"] = digest("other-core")
    elif damage == "kind":
        part["kind"] = "observed_slow"
        part["text"] = "이 구간에서는 산책 중 다른 이동 구간보다 속도가 느려졌다."
    elif damage == "meaning":
        part["text"] = "강아지가 멈춰서 냄새를 맡았다."
    else:
        card["body"] = card["writing"]["space"]["text"]
    with pytest.raises(ValueError):
        PublishedBoard.model_validate(raw)


async def test_title_receipt_must_read_the_adopted_observation():
    base = prepared()
    result = await writing.write_cards(base.input.source, base, generate=prose)
    jobs = list(result.jobs)
    index = next(i for i, j in enumerate(jobs) if j.stage == "title")
    old = jobs[index]
    payload = deepcopy(old.request)
    payload.pop("request_revision")
    card = next(c for c in payload["cards"] if "observation" in c)
    card.pop("observation")
    jobs[index] = writing.job("title", payload).model_copy(update={"accepted": old.accepted})
    changed = result.model_copy(update={"jobs": tuple(jobs)})
    receipt = StoredCardWriting(
        generation_revision=digest("test"), writer=writing.writing_version(), result=changed
    )
    with pytest.raises(ValueError, match="adopted card bodies"):
        receipt.require_bundle(result.bundle, digest("test"))


def test_http_observation_meaning_is_published_once_and_exports_app_contract(api, monkeypatch):
    client, state, _ = api

    async def generate(stage, payload, schema):
        output = await prose(stage, payload, schema)
        if stage == "title":
            cards = {c["id"]: c for c in payload["context"]}
            for title in output["titles"]:
                observed = next(
                    (
                        k
                        for k, text in CURRENT_OBSERVATION_TEXT.items()
                        if text in cards[title["id"]]["body"]
                    ),
                    None,
                )
                if observed:
                    labels = {
                        "observed_dwell": "동선이 모인 구간",
                        "observed_slow": "천천히 이어진 구간",
                        "observed_fast": "빠르게 이어진 구간",
                    }
                    title["text"] = labels[observed]
        return output

    provider = AsyncMock(side_effect=generate)
    client.app.dependency_overrides.pop(router.get_diary_writer)
    monkeypatch.setattr(writing, "generate_card_prose", provider)
    monkeypatch.setattr(collection, "configured_collection", collect_with_sgis)
    request = body(state, bundle_format="walk-diary-board-v1", preparation_budget_ms=20000)
    response = client.post(PATH, json=request)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["status"] == "ready"
    cards = [c for c in value["bundle"]["scenes"] if c["kind"] == "movement_observation"]
    assert cards
    for card in cards:
        confirmed = card["writing"]["observation"]
        assert confirmed["kind"] == card["observation"]["kind"]
        if card["writing"].get("observation_in_activity"):
            assert confirmed["text"] not in card["body"]
            assert card["writing"]["actions"][0]["movement_ids"]
        else:
            assert card["body"].startswith(confirmed["text"] + "\n")
        assert confirmed["core"] == card["core"]
        if confirmed["kind"] == "observed_dwell":
            assert card["title"] == "동선이 모인 구간"
    calls = provider.await_count
    assert (
        client.get(PATH + "?bundle_format=walk-diary-board-v1&target_scene_count=3").json() == value
    )
    assert client.post(PATH, json=request).json() == value
    assert provider.await_count == calls
    target = os.environ.get("DAENGS_OBSERVATION_FIXTURE_OUT")
    if target:
        Path(target).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
