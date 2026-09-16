"""A saved title can be reused only under its original model and title prompt."""

from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from daengs_backend.routers import walk_storyboard as router
from daengs_backend.services.walk_diary import runtime as writing
from daengs_backend.services.walk_diary.collection import service as collection
from daengs_backend.services.walk_diary.preparation.board import assemble_saved_base_board
from daengs_backend.services.walk_diary.preparation.diary import PreparedWalkDiary
from daengs_backend.services.walk_diary.storage.board import load_board, store_board
from daengs_backend.services.walk_diary.writing import policy
from daengs_walk.diary.contracts.input import DiaryInput, UserRecord, digest, material_ref
from tests.walk.diary.test_diary_board_slot_writing import prepared_case
from tests.walk.diary.test_diary_card_writing import collect_with_sgis, prose
from tests.walk.support.base_board import policy as board_policy
from tests.walk.support.diary_generation import PATH, body


def change_policy(monkeypatch, change):
    if change == "model":
        monkeypatch.setattr(policy, "MODEL", "test-new-model")
    elif change != "unchanged":
        stage = change.removesuffix("_prompt")
        monkeypatch.setattr(
            policy, "PROMPTS", {**policy.PROMPTS, stage: policy.PROMPTS[stage] + "\nNew policy."}
        )


async def new_prose(stage, payload, schema):
    result = await prose(stage, payload, schema)
    if stage == "title":
        for title in result["titles"]:
            title["text"] = "새 정책으로 작성한 제목"
    return result


def edit_note(base):
    raw = base.input.source.model_dump(mode="json")
    note = next(r for r in raw["records"] if r["content"]["kind"] == "note")
    note["content"]["text"] = "수정한 원문 그대로"
    note["ref"]["version"] = str(int(note["ref"]["version"]) + 1)
    target = material_ref(UserRecord.model_validate(note)).model_dump(mode="json")
    for background in raw["backgrounds"]:
        if background["target"]["identity"] == target["identity"]:
            background["target"] = target
    return assemble_saved_base_board(
        replace(base.input, source=DiaryInput.model_validate(raw)), board_policy(3)
    )


@pytest.mark.parametrize("reused_batch", [False, True])
@pytest.mark.parametrize("edit_original", [False, True])
@pytest.mark.parametrize("change", ["unchanged", "title_prompt", "model", "space_prompt"])
async def test_cached_titles_follow_their_own_policy(
    monkeypatch, reused_batch, edit_original, change
):
    base = prepared_case().board
    previous = await writing.write_cards(base.input.source, base, generate=prose)
    if reused_batch:
        cached = replace(base, cached_jobs=tuple(j.model_dump(mode="json") for j in previous.jobs))
        unused = AsyncMock(side_effect=AssertionError("same policy must reuse all jobs"))
        previous = await writing.write_cards(base.input.source, cached, generate=unused)
        unused.assert_not_awaited()
        assert all(j.reused and j.request["context"] for j in previous.jobs if j.stage == "title")
    else:
        assert any(len(j.request["cards"]) > 1 for j in previous.jobs if j.stage == "title")
    after = edit_note(base) if edit_original else base
    after = replace(after, cached_jobs=tuple(j.model_dump(mode="json") for j in previous.jobs))
    change_policy(monkeypatch, change)
    provider = AsyncMock(side_effect=new_prose)
    result = await writing.write_cards(after.input.source, after, generate=provider)
    stages = [call.args[0] for call in provider.call_args_list]
    # Independent note edits must not invalidate generated body/title inputs.
    changed_title = change in {"title_prompt", "model"}
    assert stages.count("title") == int(changed_title)
    assert stages.count("space") == (
        len(base.board.scenes) if change in {"model", "space_prompt"} else 0
    )
    if edit_original:
        assert any(c.writing.original_text == "수정한 원문 그대로" for c in result.bundle.scenes)
    if changed_title:
        assert all(c.title == "새 정책으로 작성한 제목" for c in result.bundle.scenes)
        assert all(not j.reused for j in result.jobs if j.stage == "title")
    else:
        assert [c.title for c in result.bundle.scenes] == [c.title for c in previous.bundle.scenes]
        assert all(j.reused for j in result.jobs if j.stage == "title")
    prepared = PreparedWalkDiary(after.input, after.plan.intermediate, after)
    stored = store_board(prepared, result.bundle, digest("new-generation"), writing=result)
    assert load_board(stored).bundle == result.bundle
    assert stored["writing_receipt"]["writer"] == policy.writing_version()
    # Fresh titles must themselves remain reusable on the next generation.
    again = replace(after, cached_jobs=tuple(j.model_dump(mode="json") for j in result.jobs))
    unused = AsyncMock(side_effect=AssertionError("current cache should be reusable"))
    repeated = await writing.write_cards(again.input.source, again, generate=unused)
    unused.assert_not_awaited()
    assert repeated.bundle == result.bundle


async def test_changed_title_policy_failure_does_not_resurrect_old_titles(monkeypatch):
    base = prepared_case().board
    previous = await writing.write_cards(base.input.source, base, generate=prose)
    cached = replace(base, cached_jobs=tuple(j.model_dump(mode="json") for j in previous.jobs))
    change_policy(monkeypatch, "title_prompt")
    provider = AsyncMock(side_effect=RuntimeError("synthetic title outage"))
    result = await writing.write_cards(base.input.source, cached, generate=provider)
    provider.assert_awaited_once()
    assert provider.call_args.args[0] == "title"
    assert all(c.writing.title_origin == "fallback" for c in result.bundle.scenes)
    assert all(
        j.failure_code == "provider_failed" and not j.reused
        for j in result.jobs
        if j.stage == "title"
    )


@pytest.mark.parametrize("change", ["title_prompt", "model"])
def test_http_keeps_publication_but_new_source_uses_new_title_policy(api, monkeypatch, change):
    client, state, _ = api
    state.envelope = None
    client.app.dependency_overrides.pop(router.get_diary_writer)
    monkeypatch.setattr(writing, "generate_card_prose", AsyncMock(side_effect=prose))
    monkeypatch.setattr(
        collection, "configured_collection", AsyncMock(side_effect=collect_with_sgis)
    )
    request = body(state, bundle_format="walk-diary-board-v1", preparation_budget_ms=20000)
    response = client.post(PATH, json=request)
    assert response.status_code == 200, response.text
    first, stored = response.json(), deepcopy(state.row.bundle)
    change_policy(monkeypatch, change)
    provider = AsyncMock(side_effect=new_prose)
    monkeypatch.setattr(writing, "generate_card_prose", provider)
    retained = client.get(PATH + "?bundle_format=walk-diary-board-v1&target_scene_count=3")
    assert retained.status_code == 200 and retained.json()["bundle"] == first["bundle"]
    assert client.post(PATH, json=request).json()["bundle"] == first["bundle"]
    assert state.row.bundle == stored
    provider.assert_not_awaited()
    state.entries[0].revision += 1
    state.entries[0].payload["note"] = "수정한 원문 그대로"
    response = client.post(
        PATH, json=body(state, bundle_format="walk-diary-board-v1", preparation_budget_ms=20000)
    )
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["status"] == "ready" and value["generation"] == first["generation"] + 1
    assert all(c["title"] == "새 정책으로 작성한 제목" for c in value["bundle"]["scenes"])
    assert sum(call.args[0] == "title" for call in provider.call_args_list) == 1
    receipt = load_board(state.row.bundle).writing_receipt
    assert receipt.writer == policy.writing_version()
    assert all(not j.reused for j in receipt.result.jobs if j.stage == "title")
