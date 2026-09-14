"""A saved diary carries its historical citations, never today's replacements."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from daengs_backend.services.walk_diary.legacy import slots as writer
from daengs_backend.services.walk_diary.legacy.board_slots import (
    complete_slot_board,
    write_legacy_slot_board,
)
from daengs_backend.services.walk_diary.lifecycle.negotiation import guard_old_writer
from daengs_backend.services.walk_diary.lifecycle.publication import (
    fallback,
    publication_reservation,
    settle_expired,
)
from daengs_backend.services.walk_diary.preparation.board import assemble_saved_base_board
from daengs_backend.services.walk_diary.preparation.diary import PreparedWalkDiary
from daengs_backend.services.walk_diary.preparation.input import InputAssembly
from daengs_backend.services.walk_diary.preparation.observations import ObservationSource
from daengs_backend.services.walk_diary.storage.board import (
    LegacyStoredBoard,
    StoredBoard,
    load_board,
    read_board,
    store_board,
)
from daengs_backend.services.walk_storyboard_state import StoryboardConflict
from daengs_evals.diary_slots_demo import demo_input
from daengs_walk.diary.board.scene_input import scene_materials
from daengs_walk.diary.contracts.input import digest
from tests.walk.support.base_board import policy


@pytest.fixture
async def saved():
    source, route, _ = demo_input()
    assembled = InputAssembly(source, (), ObservationSource(route.version, evidence=route.evidence))
    base = assemble_saved_base_board(assembled, policy(3))
    prepared = PreparedWalkDiary(assembled, base.plan.intermediate, base)

    async def generate(payload, schema):
        return {
            "scenes": [
                {
                    "scene_id": s["scene_id"],
                    "text": "주변에 등록된 공원이 있었다.",
                    "evidence_ids": [scene_materials(s)[0]["id"]],
                    "action_id": s["action"]["id"] if s["action"] else None,
                }
                for s in payload["scenes"]
            ]
        }

    output = await write_legacy_slot_board(source, base, generate)
    bundle = complete_slot_board(prepared, output)
    revision = digest("generation-one")
    stored = store_board(prepared, bundle, revision, writing=output)
    return prepared, output, stored, revision


def legacy(raw):
    return LegacyStoredBoard.model_validate(
        {
            **{
                k: v
                for k, v in raw.items()
                if k not in {"writing_receipt", "writing_receipt_sha256"}
            },
            "format": "walk-diary-board-storage-v1",
        }
    ).model_dump(mode="json")


def test_only_cited_facts_are_frozen_with_original_and_versions(saved):
    prepared, output, raw, revision = saved
    stored = StoredBoard.model_validate(raw)
    receipt = stored.writing_receipt
    assert receipt.slot_revision == prepared.board.slots.revision()
    assert receipt.writer_version == output.writer_version
    assert receipt.writer == writer.writing_version()
    written = {s.scene_id: s for s in output.writing.scenes}
    originals = {s.id: s for s in prepared.board.board.scenes}
    for scene, stamp in zip(receipt.scenes, prepared.board.slots.stamps, strict=True):
        assert scene.original_body_sha256 == digest(originals[scene.scene_id].body)
        refs = list(written[scene.scene_id].evidence_ids) if scene.scene_id in written else []
        assert [e.id for e in scene.evidence] == refs
        selected = {e.id: e for e in stamp.materials()}
        for evidence in scene.evidence:
            assert evidence.facts == selected[evidence.id].facts
            assert evidence.sources == selected[evidence.id].sources
            assert set(evidence.model_dump()) == {"id", "part", "role", "facts", "sources"}
    assert sum(len(s.evidence) for s in receipt.scenes) == 5  # Movement also supports boundaries.
    assert sum(len(s.materials()) for s in prepared.board.slots.stamps) > 3
    receipt.require_bundle(stored.bundle, revision)


def test_read_uses_saved_facts_even_after_context_and_writer_change(saved, monkeypatch):
    prepared, _, raw, revision = saved
    before = deepcopy(raw)
    changed = replace(
        prepared,
        input=replace(
            prepared.input,
            source=prepared.input.source.model_copy(
                update={"backgrounds": (), "selected_background_ids": ()}
            ),
        ),
    )
    monkeypatch.setattr(writer, "MODEL", "future-model")
    monkeypatch.setattr(writer, "PROMPT", "future-prompt")
    loaded = read_board(
        changed, SimpleNamespace(bundle=raw, input_revision=revision), digest("new")
    )
    assert loaded.model_dump(mode="json") == before
    assert raw == before


@pytest.mark.parametrize("change", ["fact", "writer", "missing_receipt", "scene", "body_hash"])
def test_corrupt_receipt_is_not_read_as_valid(saved, change):
    _, _, raw, _ = saved
    modified = deepcopy(raw)
    receipt = modified["writing_receipt"]
    if change == "fact":
        receipt["scenes"][1]["evidence"][0]["facts"]["distance_m"] = 999
    elif change == "writer":
        receipt["writer"]["model"] = "wrong-model"
    elif change == "missing_receipt":
        del modified["writing_receipt"]
    else:
        receipt["scenes"][1]["scene_id" if change == "scene" else "original_body_sha256"] = "f" * 64
        modified["writing_receipt_sha256"] = digest(receipt)
    with pytest.raises(ValueError):
        load_board(modified)


def test_receipt_cannot_move_to_another_generation_even_with_its_checksum(saved):
    _, _, raw, _ = saved
    modified = deepcopy(raw)
    modified["generation_revision"] = digest("different-generation")
    with pytest.raises(ValueError, match="another board/generation"):
        load_board(modified)


def test_legacy_receipt_is_preserved_without_backfilling_new_evidence(saved):
    prepared, _, raw, revision = saved
    old = legacy(raw)
    row = SimpleNamespace(bundle=old, input_revision=revision)
    assert read_board(prepared, row, revision).model_dump(mode="json") == old
    assert "writing_receipt" not in row.bundle
    with pytest.raises(StoryboardConflict):
        guard_old_writer(row)


@pytest.mark.parametrize("old_format", [False, True])
def test_expired_reservation_keeps_its_own_fallback_receipt(saved, old_format):
    prepared, _, _, revision = saved
    now = datetime.now(UTC)
    marker = publication_reservation(
        prepared, revision, now - timedelta(seconds=30), now - timedelta(seconds=1)
    )
    if old_format:
        marker["fallback"] = legacy(marker["fallback"])
    expected = deepcopy(marker["fallback"])
    row = SimpleNamespace(bundle=marker, generation=1, status="running", input_revision=revision)
    assert settle_expired(prepared, row, now)
    assert row.bundle == expected
    assert row.status == "ready"
    if not old_format:
        assert all(not s["evidence"] for s in row.bundle["writing_receipt"]["scenes"])


def test_store_requires_the_same_writing_and_original_edit_invalidates_read(saved):
    prepared, output, raw, revision = saved
    bundle = StoredBoard.model_validate(raw).bundle
    with pytest.raises(ValueError, match="requires its slot writing"):
        store_board(prepared, bundle, revision)
    wrong = output.model_copy(update={"slot_revision": "f" * 64})
    with pytest.raises(ValueError):
        store_board(prepared, bundle, revision, writing=wrong)
    source = prepared.input.source
    record = source.records[0]
    records = (
        record.model_copy(update={"ref": record.ref.model_copy(update={"version": "2"})}),
        *source.records[1:],
    )
    changed = replace(
        prepared,
        input=replace(prepared.input, source=source.model_copy(update={"records": records})),
    )
    assert (
        read_board(changed, SimpleNamespace(bundle=raw, input_revision=revision), revision) is None
    )
    assert fallback(prepared, revision)["writing_receipt"]["bundle_sha256"]
