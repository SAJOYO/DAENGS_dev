"""The same scene structure works with no action, an action, or protected user text."""

from unittest.mock import AsyncMock

import pytest

from daengs_backend.services import walk_diary_slot_writing as writer
from daengs_walk.diary_board_receipt import StoredSceneWriting
from daengs_walk.diary_input import DiaryInput, digest
from daengs_walk.diary_scene_input import scene_materials
from daengs_walk.diary_slots import SlotPolicy, prepare_slot_preview
from tests.walk.diary.test_diary_route_patterns import input_case
from tests.walk.support.base_board import policy


def prepared(*, action=True, note=None, slots=4):
    source, route, _ = input_case("out_back", 30, pin=action or note is not None)
    if note is not None:
        raw = source.model_dump(mode="json")
        raw["records"][0]["content"] = {"kind": "note", "text": note}
        source = DiaryInput.model_validate(raw)
    return prepare_slot_preview(
        source, SlotPolicy(route_patterns={}, motion_slots=slots), policy(3), route=route
    )


def response(payload):
    return {
        "scenes": [
            {
                "scene_id": s["scene_id"],
                "text": "지나온 길을 되짚던 중 냄새를 맡았다."
                if s["action"]
                else "지나온 길을 되짚어 걸었다.",
                "evidence_ids": [e["id"] for e in scene_materials(s)],
                "action_id": s["action"]["id"] if s["action"] else None,
            }
            for s in payload["scenes"]
        ]
    }


@pytest.mark.parametrize("action", [False, True])
async def test_scene_is_complete_with_or_without_action_and_no_base_sentence_append(action):
    preview = prepared(action=action)
    payload = writer.writing_payload(preview)
    assert payload["scenes"]
    assert any(s["action"] for s in payload["scenes"]) == action
    for s in payload["scenes"]:
        assert set(s) == {"scene_id", "mode", "scene", "action"}
        assert set(s["scene"]) == {"where", "route_pattern", "environment"}
        assert "original" not in s and "evidence" not in s
        if s["action"]:
            assert s["action"]["material"] == {"무엇을": "냄새 맡기"}
    raw = response(payload)
    provider = AsyncMock(return_value=raw)
    result = await writer.write_slot_preview(preview, provider)
    assert result.model_status == "accepted"
    expected = {s["scene_id"]: s["text"] for s in raw["scenes"]}
    for before, after in zip(preview.scenes, result.scenes, strict=True):
        assert after.body == expected.get(after.id, before.body)
        assert after.core == before.core and after.anchor == before.anchor
    assert preview.stamps == result.stamps
    provider.assert_awaited_once()


@pytest.mark.parametrize("action", [False, True])
def test_invented_or_missing_action_reference_is_rejected(action):
    preview = prepared(action=action)
    payload = writer.writing_payload(preview)
    raw = response(payload)
    target = next(i for i, s in enumerate(payload["scenes"]) if bool(s["action"]) == action)
    raw["scenes"][target]["action_id"] = None if action else "invented-action"
    with pytest.raises(ValueError, match="citation"):
        writer.accept_prose(preview, raw)


async def test_action_survives_empty_scene_materials():
    preview = prepared(slots=0)
    payload = writer.writing_payload(preview)
    assert len(payload["scenes"]) == 1
    scene = payload["scenes"][0]
    assert scene["action"] and not scene_materials(scene)
    raw = response(payload)
    raw["scenes"][0]["text"] = "냄새를 맡았다."
    result = await writer.write_slot_preview(preview, AsyncMock(return_value=raw))
    assert result.model_status == "accepted"


def test_empty_output_falls_back_without_losing_action():
    preview = prepared()
    raw = response(writer.writing_payload(preview))
    for s in raw["scenes"]:
        s.update(text="", evidence_ids=[], action_id=None)
    result = writer.accept_prose(preview, raw)
    assert result.scenes == preview.scenes


async def test_protected_note_is_not_sent_to_model_or_replaced():
    # The existing demo gives notes real spatial/environment supplements.
    from tests.walk.diary.test_diary_board_slot_writing import prepared_case

    base = prepared_case().board
    payload = writer.slot_payload(base.board, base.slots)
    assert all(s["mode"] == "preserve_original" and s["action"] is None for s in payload["scenes"])
    assert all(not s["scene"]["route_pattern"] for s in payload["scenes"])
    raw = response(payload)
    for s in raw["scenes"]:
        s["text"] = "주변에 공원이 있었다."
    result = await writer.write_slot_stamps(base.board, base.slots, AsyncMock(return_value=raw))
    board = writer.assemble_slot_writing(base.board, base.slots, result)
    for before, after in zip(base.board.scenes, board.scenes, strict=True):
        assert after.body.endswith(before.body)
    assert any(s.body.endswith("  벤치 옆에서 물을 마셨다.\n") for s in board.scenes)


def test_legacy_receipt_defaults_do_not_change_saved_hash():
    old = {
        "scene_id": "s",
        "original_body_sha256": digest("원문"),
        "background": "",
        "evidence": [],
    }
    restored = StoredSceneWriting.model_validate(old)
    assert restored.model_dump(mode="json") == old
    assert digest(restored) == digest(old)


def test_http_replacement_and_action_only_receipt_reopen_without_regeneration(api):
    from tests.walk.support.diary_generation import PATH, body

    client, state, _ = api
    state.entries[0].payload.update(kind="behavior", behavior_code="sniffing", location=None)
    state.entries[0].payload.pop("note")
    state.envelope = None
    state.provider.side_effect = lambda payload, schema: response(payload)
    request = body(state, bundle_format="walk-diary-board-v1")
    result = client.post(PATH, json=request)
    assert result.status_code == 200, result.text
    raw = result.json()
    assert raw["bundle"]["model_status"] == "accepted"
    saved = next(s for s in state.row.bundle["writing_receipt"]["scenes"] if s.get("action_id"))
    assert saved["composition"] == "replace" and not saved["evidence"]
    assert (
        client.get(PATH + "?bundle_format=walk-diary-board-v1&target_scene_count=3").json() == raw
    )
    assert client.post(PATH, json=request).json() == raw
    state.provider.assert_awaited_once()
