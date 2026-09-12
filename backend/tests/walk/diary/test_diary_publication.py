"""The first publication deadline is persisted and cannot authorize later replacement."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from daengs_backend.schemas.walk_storyboard import StoryboardRequest
from daengs_backend.services.walk_diary_generation import generate_diary
from daengs_backend.services.walk_diary_publication import within_budget
from daengs_walk.diary_board_output import BOARD_FORMAT
from tests.walk.support.diary_generation import PATH, body
from tests.walk.support.photo_input import OWNER, WALK

QUERY = f"?bundle_format={BOARD_FORMAT}&target_scene_count=3"


def request(state, **updates):
    return body(state, bundle_format=BOARD_FORMAT, **updates)


def test_zero_remaining_budget_publishes_base_without_calling_ai(api):
    client, state, _ = api
    response = client.post(PATH, json=request(state, preparation_budget_ms=0))
    assert response.status_code == 200, response.text
    first = response.json()
    assert first["status"] == "ready" and first["bundle"]["failure_code"] == "budget_exceeded"
    state.writer.assert_not_awaited()
    assert (
        client.post(PATH, json=request(state, preparation_budget_ms=10000, refresh=True)).json()
        == first
    )


@pytest.mark.parametrize("budget", [10000, 20000])
def test_fast_writer_keeps_the_same_core_list_and_publishes_early(api, budget):
    client, state, _ = api
    first = client.post(PATH, json=request(state, preparation_budget_ms=budget)).json()
    assert first["status"] == "ready" and first["bundle"]["model_status"] == "accepted"
    assert len(first["bundle"]["scenes"]) == 5
    state.provider.assert_awaited_once()


async def test_cancelled_request_is_settled_by_get_using_saved_base(api):
    client, state, db = api
    with pytest.raises(asyncio.CancelledError):
        await generate_diary(
            db,
            OWNER,
            WALK,
            StoryboardRequest.model_validate(request(state, preparation_budget_ms=10000)),
            writer=AsyncMock(side_effect=asyncio.CancelledError()),
        )
    marker = deepcopy(state.row.bundle)
    assert marker["format"] == "walk-diary-preparation-v1"
    assert client.get(PATH + QUERY).json()["status"] == "running"
    # Refreshing with another budget neither replaces the reservation nor resets its clock.
    assert (
        client.post(PATH, json=request(state, preparation_budget_ms=10000)).json()["status"]
        == "running"
    )
    assert state.row.bundle == marker
    state.row.bundle["deadline_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    completed = client.get(PATH + QUERY).json()
    assert completed["status"] == "ready"
    assert completed["bundle"] == marker["fallback"]["bundle"]
    assert client.post(PATH, json=request(state, preparation_budget_ms=10000)).json() == completed
    state.writer.assert_not_awaited()


async def test_expired_snapshot_is_not_published_after_source_edit(api):
    client, state, db = api
    with pytest.raises(asyncio.CancelledError):
        await generate_diary(
            db,
            OWNER,
            WALK,
            StoryboardRequest.model_validate(request(state, preparation_budget_ms=10000)),
            writer=AsyncMock(side_effect=asyncio.CancelledError()),
        )
    state.row.bundle["deadline_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    state.entries[0].revision += 1
    assert client.get(PATH + QUERY).json()["status"] == "stale"
    assert state.row.status == "running"


async def test_provider_ignoring_cancellation_cannot_delay_or_publish_after_timeout():
    release, late = asyncio.Event(), asyncio.Event()

    async def writer(*_):
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        late.set()
        return "late response"

    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                within_budget(writer, None, None, datetime.now(UTC) + timedelta(milliseconds=20)),
                timeout=1,
            )
        assert not late.is_set()
    finally:
        release.set()
        await asyncio.wait_for(late.wait(), 1)


def test_capability_advertises_the_twenty_second_request_limit(api):
    client, _, _ = api
    response = client.get("/app/walks/storyboard/capabilities")
    assert response.status_code == 200
    capability = response.json()["diary_publication"]
    assert capability == {"format": BOARD_FORMAT, "budget_ms": 20000}


@pytest.mark.parametrize("budget", [-1, 20001])
def test_invalid_budget_does_not_start_generation(api, budget):
    client, state, _ = api
    assert client.post(PATH, json=request(state, preparation_budget_ms=budget)).status_code == 422
    state.writer.assert_not_awaited()
