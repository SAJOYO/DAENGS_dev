"""Selected scene evidence reaches production preparation without exposing diagnostics."""

import json
from unittest.mock import Mock

import pytest

from daengs_backend.services.walk_diary.lifecycle import snapshot as snapshots
from daengs_backend.services.walk_diary.preparation import board as service
from daengs_backend.services.walk_diary.preparation.input import InputAssembly
from daengs_backend.services.walk_diary.preparation.observations import ObservationSource
from daengs_evals.diary_slots_demo import demo_input
from daengs_walk.diary.board.output import BOARD_FORMAT
from daengs_walk.diary.board.preview import prepare_slot_preview
from daengs_walk.diary.contracts.input import digest
from daengs_walk.diary.contracts.slots import SlotPolicy
from daengs_walk.diary.slots.service import prepare_board_slots
from tests.walk.support.base_board import policy, saved_case
from tests.walk.support.diary_generation import FORMAT, PATH, body
from tests.walk.support.photo_input import OWNER, WALK


def test_saved_board_prepares_all_parts_once_and_matches_preview(monkeypatch):
    source, route, _ = demo_input()
    preview = prepare_slot_preview(source, SlotPolicy(), policy(3), route=route)
    assembled = InputAssembly(source, (), ObservationSource(route.version, evidence=route.evidence))
    selector = Mock(wraps=service.prepare_base_board)
    monkeypatch.setattr(service, "prepare_base_board", selector)

    prepared = service.assemble_saved_base_board(assembled, policy(3))

    selector.assert_called_once()
    assert prepared.board == preview.base_board
    slots = prepared.slots
    assert slots.stamps == preview.stamps and slots.revision() == preview.revision
    assert slots.input_revision == source.revision()
    assert slots.plan_revision == prepared.plan.revision()
    assert slots.client_session_id == source.client_session_id
    assert [s.scene_id for s in slots.stamps] == [s.id for s in prepared.board.scenes]
    for stamp in slots.stamps[1:-1]:
        assert {item.part for item in stamp.evidence} == {"space", "environment", "motion"}
    # The refactor must retain the existing preview-v1 content digest.
    assert slots.revision() == digest(
        {
            "board": preview.base_board.plan_revision,
            "policy": preview.policy.model_dump(mode="json"),
            "stamps": [s.model_dump(mode="json") for s in preview.stamps],
        }
    )


@pytest.mark.parametrize(
    "field,value", [("client_session_id", "other-walk"), ("input_revision", "f" * 64)]
)
def test_existing_board_cannot_use_another_source_snapshot(field, value):
    source, route, _ = demo_input()
    preview = prepare_slot_preview(source, SlotPolicy(), policy(3), route=route)
    wrong = preview.base_board.model_copy(update={field: value})
    with pytest.raises(ValueError, match="source snapshot"):
        prepare_board_slots(source, wrong, SlotPolicy(), route=route)


def test_slot_policy_changes_materials_without_changing_scenes_or_source():
    source, route, _ = demo_input()
    preview = prepare_slot_preview(source, SlotPolicy(), policy(3), route=route)
    before = preview.base_board.model_dump(mode="json")
    source_before = source.model_dump(mode="json")
    slots = prepare_board_slots(
        source,
        preview.base_board,
        SlotPolicy(total_slots=0, include_location_reference=False),
        route=route,
    )
    assert all(not stamp.materials() for stamp in slots.stamps)
    assert slots.revision() != preview.revision
    assert [stamp.scene_id for stamp in slots.stamps] == [scene.id for scene in preview.scenes]
    assert preview.base_board.model_dump(mode="json") == before
    assert source.model_dump(mode="json") == source_before


def test_unlocated_board_still_has_empty_stamps_for_every_scene():
    assembled, _, _ = saved_case()
    prepared = service.assemble_saved_base_board(InputAssembly(assembled.source, ()), policy())
    assert len(prepared.board.scenes) == len(prepared.slots.stamps) == 2
    assert all(scene.anchor.point is None for scene in prepared.board.scenes)
    assert all(not stamp.materials() for stamp in prepared.slots.stamps)


@pytest.mark.parametrize("bundle_format", [BOARD_FORMAT, FORMAT])
async def test_only_board_generation_revision_tracks_slot_policy(api, monkeypatch, bundle_format):
    _, state, db = api
    _, first, first_revision = await snapshots.snapshot(db, OWNER, WALK, 3, bundle_format)
    monkeypatch.setattr(service, "SlotPolicy", lambda: SlotPolicy(total_slots=0))
    _, second, second_revision = await snapshots.snapshot(db, OWNER, WALK, 3, bundle_format)
    assert first.input.source == second.input.source
    assert first.prepared == second.prepared
    if bundle_format == BOARD_FORMAT:
        assert first.board.plan == second.board.plan
        assert first.board.board == second.board.board
        assert first.board.slots.revision() != second.board.slots.revision()
        assert first_revision != second_revision
    else:
        assert first.board is None and second.board is None
        assert first_revision == second_revision
        assert first_revision == digest(
            {
                "format": FORMAT,
                "plan": first.prepared.plan.revision(),
                "writer": snapshots.writing_version(),
            }
        )
    state.writer.assert_not_awaited()
    state.provider.assert_not_awaited()


def test_generation_fixes_slots_before_writer_and_keeps_them_private(api, monkeypatch):
    client, state, _ = api
    prepared_boards = []
    assemble = service.assemble_saved_base_board

    def capture(*args, **kwargs):
        prepared = assemble(*args, **kwargs)
        prepared_boards.append(prepared)
        return prepared

    monkeypatch.setattr(service, "assemble_saved_base_board", capture)

    async def before_write():
        assert len(prepared_boards) == 1
        prepared = prepared_boards[0]
        assert prepared.slots.input_revision == prepared.input.source.revision()
        assert any(e.part == "space" for s in prepared.slots.stamps for e in s.evidence)

    state.before_write = before_write
    response = client.post(PATH, json=body(state, bundle_format=BOARD_FORMAT))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "ready"
    assert result["bundle"]["model_status"] == "accepted"
    state.provider.assert_awaited_once()
    assert len(prepared_boards) == 2  # Re-read after writing checks the same generation input.
    assert prepared_boards[0].slots == prepared_boards[1].slots
    assert [s["id"] for s in result["bundle"]["scenes"]] == [
        s.scene_id for s in prepared_boards[0].slots.stamps
    ]
    for name in ("slots", "stamps", "evidence", "decisions", "diagnostics", "slot_policy"):
        assert f'"{name}"' not in response.text
    for name in ("slots", "stamps", "decisions", "diagnostics"):
        assert f'"{name}"' not in json.dumps(state.row.bundle)

    # Policy updates must not silently replace an already published diary.
    monkeypatch.setattr(service, "SlotPolicy", lambda: SlotPolicy(total_slots=0))
    latest = client.get(PATH + f"?bundle_format={BOARD_FORMAT}&target_scene_count=3").json()
    assert latest["status"] == "ready" and latest["bundle"] == result["bundle"]
    assert latest["generation"] == result["generation"]
    assert latest["background_update_available"] is True
    state.provider.assert_awaited_once()
