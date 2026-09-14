"""First HTTP publication starts ready actions despite pending entry-context jobs."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from daengs_backend.routers import walk_storyboard as router
from daengs_backend.services import walk_diary_card_writing as writing
from daengs_backend.services import walk_diary_input as reader
from daengs_backend.services import walk_diary_space_collection as collection
from tests.walk.diary.test_diary_card_writing import collect_with_sgis, prose
from tests.walk.support.diary_generation import PATH, body


@pytest.fixture
def pending_api(api, monkeypatch):
    client, state, db = api
    state.walk.created_at = datetime.now(UTC)
    state.finished_background, state.envelope = state.envelope, None
    state.context_job = SimpleNamespace(state="running")
    state.context_jobs[state.entries[0].id] = [state.context_job]
    monkeypatch.setattr(
        reader.contexts,
        "current",
        AsyncMock(
            side_effect=lambda _session, row, **kw: (
                state.context_jobs.get(row.id, []),
                {"space.facility": SimpleNamespace(envelope=state.envelope)}
                if row.id == state.entries[0].id and state.envelope is not None
                else {},
            )
        ),
    )
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
    # Keep routing, negotiation, reservation, graph and publication real.
    client.app.dependency_overrides.pop(router.get_diary_writer)
    return client, state, db


@pytest.mark.parametrize("job_state", ["pending", "running"])
@pytest.mark.parametrize("space_result", ["available", "unavailable", "timeout"])
def test_pending_context_does_not_block_action_or_first_publication(
    pending_api, monkeypatch, job_state, space_result
):
    client, state, db = pending_api
    state.context_job.state = job_state
    action_started, collection_started, collection_cancelled = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )

    async def generate(stage, payload, schema):
        assert state.row.status == "running" and db.commit.await_count >= 1
        if stage == "action":
            assert state.context_job.state == job_state
            action_started.set()
        return await prose(stage, payload, schema)

    async def collect(board):
        assert state.row.status == "running" and db.commit.await_count >= 1
        assert state.row.bundle["format"] == "walk-diary-preparation-v1"
        marker = state.row.bundle
        assert (
            datetime.fromisoformat(marker["deadline_at"])
            - datetime.fromisoformat(marker["started_at"])
        ).total_seconds() == 2
        collection_started.set()
        # Completion is causally dependent on the action starting, not a timing guess.
        await action_started.wait()
        if space_result == "available":
            return await collect_with_sgis(board)
        if space_result == "unavailable":
            raise OSError("synthetic collection outage")
        try:
            await asyncio.Event().wait()  # The existing body deadline must stop this wait.
        finally:
            collection_cancelled.set()

    provider, collector = AsyncMock(side_effect=generate), AsyncMock(side_effect=collect)
    monkeypatch.setattr(writing, "generate_card_prose", provider)
    monkeypatch.setattr(collection, "configured_collection", collector)
    request = body(state, bundle_format="walk-diary-board-v1", preparation_budget_ms=2000)
    assert (
        client.get(PATH + "?bundle_format=walk-diary-board-v1&target_scene_count=3").json()[
            "status"
        ]
        == "pending"
    )
    response = client.post(PATH, json=request)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["status"] == "ready" and value["generation"] == 1
    assert action_started.is_set() and collection_started.is_set()
    assert collection_cancelled.is_set() == (space_result == "timeout")
    collector.assert_awaited_once()
    action = next(c for c in value["bundle"]["scenes"] if c["writing"]["actions"])
    assert action["writing"]["actions"][0]["origin"] == "generated"
    assert "보리가 냄새를 맡았다." in action["body"]
    assert action["writing"]["space"]["origin"] == (
        "generated" if space_result == "available" else "fallback"
    )
    note = next(c for c in value["bundle"]["scenes"] if c["writing"]["original_text"])
    assert note["body"].endswith(state.entries[0].payload["note"])
    assert state.context_job.state == job_state
    saved, calls = deepcopy(state.row.bundle), provider.await_count
    # Late entry-context completion supplies new background, never replacement authority.
    state.context_job.state, state.envelope = "completed", state.finished_background
    for response in (
        client.get(PATH + "?bundle_format=walk-diary-board-v1&target_scene_count=3"),
        client.post(PATH, json={**request, "refresh": True}),
    ):
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["status"] == "ready" and result["generation"] == 1
        assert result["bundle"] == value["bundle"]
    assert state.row.bundle == saved and provider.await_count == calls
    collector.assert_awaited_once()


def test_pending_context_with_exhausted_budget_publishes_base_without_external_work(
    pending_api, monkeypatch
):
    client, state, _ = pending_api
    provider, collector = AsyncMock(), AsyncMock()
    monkeypatch.setattr(writing, "generate_card_prose", provider)
    monkeypatch.setattr(collection, "configured_collection", collector)
    request = body(state, bundle_format="walk-diary-board-v1", preparation_budget_ms=0)
    value = client.post(PATH, json=request).json()
    assert value["status"] == "ready" and value["generation"] == 1
    assert value["bundle"]["failure_code"] == "budget_exceeded"
    assert client.post(PATH, json={**request, "preparation_budget_ms": 10000}).json() == value
    provider.assert_not_awaited()
    collector.assert_not_awaited()


def test_source_edit_after_pending_context_entry_still_blocks_publication(pending_api, monkeypatch):
    client, state, _ = pending_api

    async def generate(stage, payload, schema):
        if stage == "title":
            state.entries[0].revision += 1
            state.entries[0].payload["note"] = "작성 중 바꾼 원문"
        return await prose(stage, payload, schema)

    provider = AsyncMock(side_effect=generate)
    monkeypatch.setattr(writing, "generate_card_prose", provider)
    monkeypatch.setattr(collection, "configured_collection", AsyncMock(side_effect=OSError))
    value = client.post(
        PATH, json=body(state, bundle_format="walk-diary-board-v1", preparation_budget_ms=2000)
    ).json()
    assert value["status"] == "stale" and value["bundle"] is None
    assert state.row.status != "ready"
    assert any(call.args[0] == "action" for call in provider.call_args_list)
