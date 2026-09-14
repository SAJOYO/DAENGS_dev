"""Changing provider dependencies or wrapping a writer must not select another strategy."""

from datetime import UTC, datetime
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from daengs_backend.config import settings
from daengs_backend.routers import walk_storyboard as router
from daengs_backend.schemas.walk_storyboard import StoryboardRequest
from daengs_backend.services.walk_diary import contracts as diary_contracts
from daengs_backend.services.walk_diary import runtime as cards
from daengs_backend.services.walk_diary.collection import service as collection
from daengs_backend.services.walk_diary.lifecycle.generation import generate_diary
from daengs_backend.services.walk_diary.runtime import write_board
from tests.walk.diary.test_diary_card_writing import (
    collect_with_sgis,
    prepared,
    prose,
    sgis_response,
)
from tests.walk.support.diary_generation import PATH, body
from tests.walk.support.photo_input import OWNER, WALK


async def test_injected_model_keeps_card_jobs_and_card_receipt(monkeypatch):
    base = prepared()
    provider = AsyncMock(side_effect=prose)
    collect = AsyncMock(side_effect=collect_with_sgis)
    monkeypatch.setattr(collection, "configured_collection", collect)
    result = await write_board(base.input.source, base, generate=provider)
    assert isinstance(result, diary_contracts.CardWritingResult)
    assert {job.stage for job in result.jobs} == {"space", "action", "title"}
    assert provider.call_args_list[-1].args[0] == "title"
    collect.assert_awaited_once()
    assert result.scene_backgrounds is not None


async def test_injected_card_collector_keeps_graph_and_does_not_call_default(monkeypatch):
    base = prepared()
    configured = AsyncMock(side_effect=AssertionError("injected collector was ignored"))
    monkeypatch.setattr(collection, "configured_collection", configured)
    collect = AsyncMock(side_effect=collect_with_sgis)
    result = await write_board(base.input.source, base, generate=prose, collector=collect)
    assert isinstance(result, diary_contracts.CardWritingResult)
    assert {job.stage for job in result.jobs} == {"space", "action", "title"}
    collect.assert_awaited_once()
    configured.assert_not_awaited()


async def test_legacy_collection_is_explicit_and_keeps_its_snapshot(api, monkeypatch):
    _, state, db = api
    monkeypatch.setattr(settings, "walk_diary_space_enabled", True)
    state.envelope = None
    order = []

    async def collect(board):
        assert state.row is None and db.commit.await_count == 1
        order.append("collect")
        return await collect_with_sgis(board)

    async def before_write():
        assert state.row.status == "running"
        order.append("write")

    state.before_write = before_write
    spy = AsyncMock(side_effect=collect)
    result = await generate_diary(
        db,
        OWNER,
        WALK,
        StoryboardRequest.model_validate(body(state, bundle_format="walk-diary-board-v1")),
        writer=state.slot_writer,
        legacy_collector=spy,
    )
    assert result.status == "ready" and result.bundle.model_status == "accepted"
    assert order == ["collect", "write"]
    spy.assert_awaited_once()
    state.provider.assert_awaited_once()
    assert state.row.bundle["scene_backgrounds"] is not None


@pytest.mark.parametrize("mode", ["default", "direct", "partial", "wrapped"])
@pytest.mark.parametrize("outage", [False, True])
def test_wrapping_current_writer_keeps_one_collection_after_reservation(
    api, monkeypatch, mode, outage
):
    client, state, db = api
    collect_spaces = collection.collect_spaces
    state.walk.created_at = datetime.now(UTC)
    state.context_jobs[state.entries[0].id] = [SimpleNamespace(state="running")]
    monkeypatch.setattr(settings, "walk_diary_space_enabled", True)
    client.app.dependency_overrides.pop(router.get_diary_writer)

    async def wrapped(source, base):
        return await write_board(source, base)

    if mode != "default":
        selected = {"direct": write_board, "partial": partial(write_board), "wrapped": wrapped}[
            mode
        ]
        client.app.dependency_overrides[router.get_diary_writer] = lambda: selected

    async def collect(board, **_):
        assert state.row is not None and state.row.status == "running"
        assert db.commit.await_count >= 1
        if outage:
            raise OSError("synthetic collection outage")
        return await collect_spaces(
            board,
            transport=httpx.MockTransport(sgis_response),
            include_sgis=True,
            sgis_key="test-only",
            sgis_secret="test-only",
        )

    spy = AsyncMock(side_effect=collect)
    # Patch acquisition itself: the old lifecycle imported configured_collection
    # separately, so replacing only that module attribute misses its extra call.
    monkeypatch.setattr(collection, "collect_spaces", spy)
    monkeypatch.setattr(cards, "generate_card_prose", AsyncMock(side_effect=prose))
    request = body(state, bundle_format="walk-diary-board-v1", preparation_budget_ms=20000)
    response = client.post(PATH, json=request)
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "ready"
    assert all("writing" in scene for scene in result["bundle"]["scenes"])
    assert state.row.bundle["format"] == "walk-diary-board-storage-v2"
    assert client.post(PATH, json=request).json() == result
    assert (
        client.get(PATH + "?bundle_format=walk-diary-board-v1&target_scene_count=3").json()
        == result
    )
    spy.assert_awaited_once()
