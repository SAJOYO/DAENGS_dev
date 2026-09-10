"""Negotiated board API uses the existing reservation, private JSONB and old writer."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from daengs_backend.schemas.walk_storyboard import StoryboardRequest
from daengs_backend.services import walk_diary_writing as writer
from daengs_backend.services.walk_diary_board_storage import StoredBoard
from daengs_backend.services.walk_diary_generation import generate_diary
from daengs_walk.diary_board_output import BOARD_FORMAT, BOARD_RESPONSE, PublishedBoard
from tests.walk.support.diary_generation import PATH, body
from tests.walk.support.observations import stored, uploaded
from tests.walk.support.photo_input import OWNER, WALK

QUERY = f"?bundle_format={BOARD_FORMAT}&target_scene_count=3"


def request(state, **updates):
    return body(state, bundle_format=BOARD_FORMAT, **updates)


def test_fixed_board_reuses_existing_writer_and_hides_private_input(api):
    client, state, _ = api
    assert client.get(PATH + QUERY).json()["status"] == "pending"
    response = client.post(PATH, json=request(state))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["format"] == BOARD_RESPONSE and result["status"] == "ready"
    assert result["bundle"]["format"] == BOARD_FORMAT
    assert len(result["bundle"]["scenes"]) == 5
    scenes = result["bundle"]["scenes"]
    assert scenes[0]["boundary"] == "start" and scenes[-1]["boundary"] == "end"
    record = next(s for s in scenes if s["kind"] == "user_record")
    assert record["body"].endswith(state.entries[0].payload["note"])
    assert "등록된 카페" in record["body"]
    assert "pin_payload" not in response.text and "owner_id" not in response.text
    assert "background_decisions" not in response.text and "record" not in record["core"]
    state.provider.assert_awaited_once()
    assert state.row.bundle["format"] == "walk-diary-board-storage-v1"
    assert StoredBoard.model_validate(state.row.bundle).bundle == PublishedBoard.model_validate(
        result["bundle"]
    )
    assert client.get(PATH + QUERY).json() == result
    assert client.post(PATH, json=request(state, refresh=True)).json() == result
    state.provider.assert_awaited_once()


def test_no_action_ordinary_route_reaches_api_as_checkpoints_not_fake_observations(api):
    client, state, _ = api
    state.walk, state.analysis, _ = stored(uploaded([(i * 10, i * 20) for i in range(101)]))
    state.entries = []
    result = client.post(PATH, json=request(state)).json()
    assert result["status"] == "ready", result
    assert result["preparation_counts"]["checkpoints"] == 3
    assert [s["kind"] for s in result["bundle"]["scenes"]] == [
        "session_boundary",
        *("route_checkpoint",) * 3,
        "session_boundary",
    ]
    assert all(s["body"] for s in result["bundle"]["scenes"])
    state.provider.assert_not_awaited()


def test_saved_v1_is_read_without_conversion_or_generation(api):
    client, state, _ = api
    first = client.post(PATH, json=body(state)).json()
    saved = deepcopy(state.row.bundle)
    # New client target differs; the saved policy is retained with the saved v1 board.
    assert client.get(PATH + QUERY.replace("=3", "=5")).json() == first
    assert client.post(PATH, json=request(state, refresh=True)).json() == first
    assert state.row.bundle == saved
    state.provider.assert_awaited_once()


def test_saved_legacy_is_returned_in_its_original_format(api):
    client, state, _ = api
    first = client.post(
        PATH,
        json={
            "bundle_format": "walk-storyboard-candidates-v2",
            "expected_entries": {str(e.id): e.revision for e in state.entries},
        },
    ).json()
    saved = deepcopy(state.row.bundle)
    assert client.get(PATH + QUERY).json() == first
    assert client.post(PATH, json=request(state, refresh=True)).json() == first
    assert state.row.bundle == saved
    state.writer.assert_not_awaited()


def test_new_board_cannot_be_replaced_by_an_old_client(api):
    client, state, _ = api
    first = client.post(PATH, json=request(state)).json()
    saved = deepcopy(state.row.bundle)
    assert client.post(PATH, json=body(state, refresh=True)).status_code == 409
    assert (
        client.post(
            PATH, json={"expected_entries": {str(e.id): e.revision for e in state.entries}}
        ).status_code
        == 409
    )
    assert state.row.bundle == saved and state.row.generation == first["generation"]


def test_policy_and_background_updates_keep_published_board_but_source_edit_does_not(
    api, monkeypatch
):
    client, state, _ = api
    first = client.post(PATH, json=request(state)).json()
    saved = deepcopy(state.row.bundle)
    monkeypatch.setattr(writer, "MODEL", "new-policy-model")
    state.envelope = {**state.envelope, "id": "later-background"}
    later = client.get(PATH + QUERY.replace("=3", "=4")).json()
    assert later["status"] == "ready" and later["bundle"] == first["bundle"]
    assert later["target_scene_count"] == 3 and later["background_update_available"]
    assert state.row.bundle == saved
    state.entries[0].revision += 1
    state.entries[0].payload["note"] = "새 기록"
    assert client.get(PATH + QUERY).json()["status"] == "stale"


def test_unexpected_writer_error_still_publishes_prepared_base_board(api):
    client, state, _ = api
    state.writer.side_effect = RuntimeError("private detail")
    result = client.post(PATH, json=request(state)).json()
    assert result["status"] == "ready" and result["bundle"]["model_status"] == "unavailable"
    assert len(result["bundle"]["scenes"]) == 5
    assert "private detail" not in str(result)


def test_late_source_change_cannot_publish_and_old_client_cannot_steal_new_lease(api):
    client, state, _ = api

    async def change():
        state.entries[0].revision += 1

    state.before_write = change
    result = client.post(PATH, json=request(state)).json()
    assert result["status"] == "stale" and result["bundle"] is None
    assert state.row.bundle == {
        "format": "walk-diary-reservation-v1",
        "bundle_format": BOARD_FORMAT,
    }
    assert client.post(PATH, json=body(state)).status_code == 409
    assert client.post(PATH, json=request(state)).json()["status"] == "running"
    state.row.updated_at = datetime.now(UTC) - timedelta(seconds=61)
    state.before_write = None
    assert client.post(PATH, json=request(state)).json()["generation"] == 2


async def test_request_cancellation_preserves_new_format_lease(api):
    import asyncio

    _, state, db = api
    with pytest.raises(asyncio.CancelledError):
        await generate_diary(
            db,
            OWNER,
            WALK,
            StoryboardRequest.model_validate(request(state)),
            writer=AsyncMock(side_effect=asyncio.CancelledError()),
        )
    assert state.row.status == "running" and state.row.bundle["bundle_format"] == BOARD_FORMAT


def test_corrupt_receipt_and_wrong_expected_records_are_not_accepted(api):
    client, state, _ = api
    assert client.post(PATH, json=request(state, expected_entries={})).status_code == 409
    client.post(PATH, json=request(state))
    state.row.bundle["bundle"]["scenes"][0]["body"] = "tampered"
    response = client.get(PATH + QUERY).json()
    assert response["status"] == "failed" and response["bundle"] is None
