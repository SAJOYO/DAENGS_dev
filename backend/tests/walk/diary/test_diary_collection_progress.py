"""Partial acquisition survives deadlines/projection errors without adopting late responses."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest

from daengs_backend.orchestration.execution import JobExecutor
from daengs_backend.services.walk_diary import runtime as writing
from daengs_backend.services.walk_diary.collection import application
from daengs_backend.services.walk_diary.collection import service as collection
from daengs_backend.services.walk_diary.collection.progress import active_collection
from daengs_backend.services.walk_diary.deadline import publication_deadline
from daengs_backend.services.walk_diary.preparation.board import with_scene_backgrounds
from daengs_backend.services.walk_diary.preparation.diary import PreparedWalkDiary
from daengs_backend.services.walk_diary.storage.board import load_board, store_board
from daengs_walk.diary_input import digest
from tests.walk.diary.test_diary_card_writing import collect_with_sgis, prepared, prose
from tests.walk.diary.test_diary_space_integration import public_response


async def test_deadline_does_not_wait_for_cancellation_and_other_sources_get_a_lane(monkeypatch):
    base = prepared()
    release, cancelled, done = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def response(request):
        if request.url.host == "api.mcee.go.kr":
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()  # Deliberately uncooperative provider.
            finally:
                done.set()
        return public_response(request)

    def missing(*_):
        raise ValueError("no catalog")

    monkeypatch.setattr(collection, "cached_area", missing)
    try:
        snapshot = await asyncio.wait_for(
            collection.collect_spaces(
                base.board,
                commerce_key="test-only",
                timeout_s=0.3,
                transport=httpx.MockTransport(response),
            ),
            timeout=1.5,
        )
        await asyncio.wait_for(cancelled.wait(), 0.5)
        before = snapshot.model_dump(mode="json")
        land = [b for b in snapshot.backgrounds if b.provider.endswith("land_cover")]
        assert any(b.reason == "collection_timeout" for b in land)
        assert any(b.reason == "collection_not_started" for b in land)
        assert all(
            b.status == "known" for b in snapshot.backgrounds if b.provider.endswith("commerce")
        )
        assert all(
            b.reason == "source_unavailable"
            for b in snapshot.backgrounds
            if b.provider.endswith("park")
        )
        release.set()
        await asyncio.wait_for(done.wait(), 0.5)
        await asyncio.sleep(0)
        assert snapshot.model_dump(mode="json") == before
    finally:
        release.set()


@pytest.mark.parametrize("failure", ["timeout", "error"])
async def test_outer_collection_failure_keeps_completed_sources_and_receipt(failure):
    base = prepared()
    acquired = None

    async def collector(board):
        nonlocal acquired
        acquired = await collect_with_sgis(board)
        if failure == "error":
            raise OSError("synthetic cleanup failure, never logged")
        await asyncio.Event().wait()

    token = publication_deadline.set(datetime.now(UTC) + timedelta(seconds=1.5))
    try:
        result = await writing.write_cards(
            base.input.source, base, generate=prose, collector=collector
        )
    finally:
        publication_deadline.reset(token)
    assert active_collection.get() is None
    assert result.collection_receipt.status == failure
    assert result.collection_receipt.snapshot == acquired
    assert result.scene_backgrounds == acquired
    assert any(c.place_reference for c in result.bundle.scenes)
    assert any(j.request["materials"] for j in result.jobs if j.stage == "space")
    bound = with_scene_backgrounds(base, result.scene_backgrounds)
    saved = store_board(
        PreparedWalkDiary(base.input, base.plan.intermediate, bound),
        result.bundle,
        digest("partial-generation"),
        writing=result,
    )
    assert load_board(saved).writing_receipt.result.collection_receipt == result.collection_receipt
    saved["writing_receipt"]["result"]["collection_receipt"]["status"] = "completed"
    with pytest.raises(ValueError):
        load_board(saved)


async def test_one_application_error_preserves_acquired_payload_and_other_sources(monkeypatch):
    base = prepared()
    snapshot = await collect_with_sgis(base.board)
    rejected = next(b for b in snapshot.backgrounds if b.provider == "sgis")
    original = application.with_scene_backgrounds

    def project(value, collected):
        if any(b.id == rejected.id for b in collected.backgrounds):
            raise ValueError("synthetic bad projection")
        return original(value, collected)

    monkeypatch.setattr(application, "with_scene_backgrounds", project)
    result = await writing.write_cards(
        base.input.source, base, generate=prose, collector=AsyncMock(return_value=snapshot)
    )
    receipt = result.collection_receipt
    assert receipt.status == "completed" and receipt.application_status == "partial"
    assert receipt.application_failures == (rejected.id,)
    assert (
        receipt.snapshot == snapshot
    )  # Includes the acquired address that could not be projected.
    assert rejected.id not in {b.id for b in result.scene_backgrounds.backgrounds}
    assert len(result.scene_backgrounds.backgrounds) == len(snapshot.backgrounds) - 1
    assert any(c.place_reference for c in result.bundle.scenes)
    bound = with_scene_backgrounds(base, result.scene_backgrounds)
    stored = store_board(
        PreparedWalkDiary(base.input, base.plan.intermediate, bound),
        result.bundle,
        digest("application-generation"),
        writing=result,
    )
    assert load_board(stored).bundle == result.bundle


async def test_every_application_error_keeps_raw_snapshot_and_original_records(monkeypatch):
    base = prepared()
    snapshot = await collect_with_sgis(base.board)

    def broken(*_):
        raise RuntimeError("synthetic application outage")

    monkeypatch.setattr(application, "with_scene_backgrounds", broken)
    result = await writing.write_cards(
        base.input.source, base, generate=prose, collector=AsyncMock(return_value=snapshot)
    )
    assert result.collection_receipt.application_status == "failed"
    assert result.collection_receipt.snapshot == snapshot
    assert result.collection_receipt.application_failures == tuple(
        b.id for b in snapshot.backgrounds
    )
    assert result.scene_backgrounds is None
    assert [c.core for c in result.bundle.scenes] == [c.core_ref for c in base.board.scenes]


async def test_concurrent_walks_do_not_share_progress_or_snapshots():
    first = prepared()
    source = first.input.source.model_copy(
        update={"client_session_id": "another-synthetic-session"}
    )
    from daengs_backend.services.walk_diary.preparation.board import assemble_saved_base_board
    from tests.walk.support.base_board import policy

    second = assemble_saved_base_board(replace(first.input, source=source), policy(3))

    async def fail_after_collection(board):
        await collect_with_sgis(board)
        raise OSError("synthetic failure after progress")

    results = await asyncio.gather(
        *(
            writing.write_cards(b.input.source, b, generate=prose, collector=fail_after_collection)
            for b in (first, second)
        )
    )
    for base, result in zip((first, second), results, strict=True):
        assert result.collection_receipt.snapshot.validate_board(base.board)
        assert result.bundle.client_session_id == base.board.client_session_id
    assert active_collection.get() is None


async def test_foreign_atomic_snapshot_is_not_adopted():
    base = prepared()
    snapshot = await collect_with_sgis(base.board)
    foreign = snapshot.model_copy(update={"board_revision": digest("different-walk")})
    _, receipt = await application.collect_for_writing(
        base,
        AsyncMock(return_value=foreign),
        JobExecutor(),
        asyncio.get_running_loop().time() + 1,
    )
    assert receipt.failure_code == "invalid_snapshot"
    assert not receipt.snapshot.backgrounds


async def test_outer_deadline_keeps_progress_before_collector_returns(monkeypatch):
    base = prepared()
    returned = False

    def missing(*_):
        raise ValueError("no catalog")

    async def response(request):
        if request.url.host == "api.mcee.go.kr":
            await asyncio.Event().wait()
        return public_response(request)

    async def collector(board):
        nonlocal returned
        snapshot = await collection.collect_spaces(
            board,
            commerce_key="test-only",
            transport=httpx.MockTransport(response),
            timeout_s=30,
        )
        await asyncio.Event().wait()  # Even a completed snapshot cannot leave through return.
        returned = True
        return snapshot

    monkeypatch.setattr(collection, "cached_area", missing)
    bound, receipt = await application.collect_for_writing(
        base,
        collector,
        JobExecutor(),
        asyncio.get_running_loop().time() + 0.5,
    )
    assert not returned and receipt.status == "timeout"
    assert any(
        b.provider.endswith("commerce") and b.status == "known"
        for b in receipt.snapshot.backgrounds
    )
    assert any(s.evidence for s in bound.slots.stamps)
    assert any(b.reason == "collection_timeout" for b in receipt.snapshot.backgrounds)


async def test_zero_budget_does_not_start_any_provider():
    base = prepared()
    provider = AsyncMock(side_effect=AssertionError("must not call provider"))
    snapshot = await collection.collect_spaces(
        base.board, timeout_s=0, transport=httpx.MockTransport(provider)
    )
    assert snapshot.backgrounds
    assert all(b.reason == "collection_not_started" for b in snapshot.backgrounds)
    provider.assert_not_awaited()
