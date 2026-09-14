"""Degraded acquisition receipts survive JSONB and a fresh connection without new collection."""

from copy import deepcopy

import pytest

from daengs_backend.models.walk_storyboard import WalkStoryboard
from daengs_backend.services.walk_diary.collection import application
from daengs_backend.services.walk_diary.lifecycle.generation import generate_diary, get_diary
from daengs_backend.services.walk_diary.runtime import write_board
from daengs_backend.services.walk_diary.storage.board import load_board
from daengs_walk.diary.board.output import BOARD_FORMAT
from tests.walk.diary.test_diary_board_db import (
    board_database,  # noqa: F401 -- shared disposable database fixture
    save_context_record,
    spec,
)
from tests.walk.diary.test_diary_card_writing import collect_with_sgis, prose
from tests.walk.support.entry_v2 import ENTRY, OWNER, WALK


@pytest.mark.parametrize("failure", ["collection", "application"])
async def test_partial_receipt_round_trips_without_recollection(
    board_database,  # noqa: F811 -- imported pytest fixture injection
    monkeypatch,
    failure,
):
    factory = board_database
    await save_context_record(factory)
    calls = 0

    async def collect(board):
        snapshot = await collect_with_sgis(board)
        if failure == "collection":
            raise OSError("synthetic cleanup failure")
        return snapshot

    original = application.with_scene_backgrounds

    def project(base, snapshot):
        if failure == "application" and any(
            b.provider == "sgis" and b.status == "known" for b in snapshot.backgrounds
        ):
            raise ValueError("synthetic SGIS projection failure")
        return original(base, snapshot)

    monkeypatch.setattr(application, "with_scene_backgrounds", project)

    async def writer(source, base):
        nonlocal calls
        calls += 1
        return await write_board(source, base, generate=prose, collector=collect)

    request = spec(expected_entries={str(ENTRY): 1}, preparation_budget_ms=20000)
    async with factory() as db:
        first = await generate_diary(db, OWNER, WALK, request, writer=writer)
    assert first.status == "ready" and first.bundle.model_status == "accepted"
    assert any(s.writing.original_text == "  있는 그대로\n  " for s in first.bundle.scenes)
    async with factory() as db:
        raw = deepcopy((await db.get(WalkStoryboard, WALK)).bundle)
        receipt = load_board(raw).writing_receipt.result.collection_receipt
    assert any(b.provider == "sgis" and b.status == "known" for b in receipt.snapshot.backgrounds)
    if failure == "collection":
        assert receipt.failure_code == "collector_failed"
        assert any(s.place_reference for s in first.bundle.scenes)
    else:
        assert receipt.application_status == "partial"
        assert receipt.application_failures
    async with factory() as db:
        assert await get_diary(db, OWNER, WALK, 3, BOARD_FORMAT) == first
    async with factory() as db:
        assert await generate_diary(db, OWNER, WALK, request, writer=writer) == first
        assert (await db.get(WalkStoryboard, WALK)).bundle == raw
    assert calls == 1
    assert "collection_receipt" not in first.model_dump_json()
