"""Keep finished boards on background changes without reviving changed source records."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from daengs_backend.config import settings
from daengs_backend.services import walk_diary_input as reader
from tests.walk.support.diary_generation import PATH, QUERY, body
from tests.walk.support.photo_input import row as photos


def test_background_updates_keep_exact_board_and_never_spend_without_refresh(api):
    client, state, _ = api
    first = client.post(PATH, json=body(state)).json()
    assert first["background_update_available"] is False
    original_storage = deepcopy(state.row.bundle)
    state.envelope = {**state.envelope, "id": str(uuid4())}
    for _ in range(2):
        read = client.get(PATH + QUERY).json()
        assert read == {**first, "background_update_available": True}
        assert client.post(PATH, json=body(state)).json() == read
    assert state.row.bundle == original_storage
    state.writer.assert_awaited_once()
    refreshed = client.post(PATH, json=body(state, refresh=True)).json()
    assert refreshed["status"] == "ready" and refreshed["generation"] == 2
    assert refreshed["background_update_available"] is False
    assert refreshed["input_revision"] != first["input_revision"]
    assert refreshed["bundle"]["input_revision"] != first["bundle"]["input_revision"]
    assert state.writer.await_count == 2


def test_later_provider_unavailability_does_not_rewrite_saved_prose(api):
    client, state, _ = api
    first = client.post(PATH, json=body(state)).json()
    state.envelope = {
        **state.envelope,
        "id": str(uuid4()),
        "status": "unavailable",
        "reason": "provider_unavailable",
        "payload": None,
        "payload_sha256": None,
    }
    read = client.get(PATH + QUERY).json()
    assert read == {**first, "background_update_available": True}
    assert read["bundle"]["scenes"][0]["narration"]["status"] == "generated"
    state.writer.assert_awaited_once()


@pytest.mark.parametrize(
    "change",
    [
        "note",
        "entry_revision",
        "delete",
        "pin",
        "photo",
        "route",
        "ended_at",
        "pet",
    ],
)
def test_source_change_is_stale_even_when_background_also_changes(api, change):
    client, state, _ = api
    client.post(PATH, json=body(state))
    state.envelope = {**state.envelope, "id": str(uuid4())}
    row = state.entries[0]
    if change == "note":
        row.payload["note"] = "원문 수정"
    elif change == "entry_revision":
        row.revision += 1
    elif change == "delete":
        row.revision += 1
        row.payload = None
    elif change == "pin":
        row.payload["location"]["lat"] += 0.0001
    elif change == "photo":
        state.photo = photos()
    elif change == "route":
        state.analysis.id = uuid4()
    elif change == "ended_at":
        from datetime import timedelta

        state.walk.ended_at += timedelta(seconds=1)
    else:
        state.walk.pet_ids = [uuid4()]
    response = client.get(PATH + QUERY)
    assert response.status_code == 200, response.text
    read = response.json()
    assert read["status"] == "stale" and read["bundle"] is None
    assert read["background_update_available"] is False
    state.writer.assert_awaited_once()


def test_deleted_photo_manifest_does_not_resurrect_a_photo(api):
    client, state, _ = api
    state.photo = photos()
    first = client.post(PATH, json=body(state)).json()
    assert any(
        s["user_record"] and s["user_record"]["kind"] == "photo" for s in first["bundle"]["scenes"]
    )
    state.photo.revision += 1
    state.photo.records = []
    read = client.get(PATH + QUERY).json()
    assert read["status"] == "stale" and read["bundle"] is None


def test_pin_sidecar_revision_alone_invalidates_finished_board(api, monkeypatch):
    client, state, _ = api
    pin = SimpleNamespace(entry_id=state.entries[0].id, pin_revision=1, payload=None)
    monkeypatch.setattr(settings, "walk_entry_v2_enabled", True)
    monkeypatch.setattr(reader.pins, "pins", AsyncMock(return_value=[pin]))
    first = client.post(PATH, json=body(state)).json()
    assert first["status"] == "ready"
    pin.pin_revision += 1
    read = client.get(PATH + QUERY).json()
    assert read["entry_revisions"] == first["entry_revisions"]
    assert read["status"] == "stale" and read["bundle"] is None


@pytest.mark.parametrize("change", ["bundle", "receipt", "session"])
def test_corrupt_or_cross_session_storage_is_not_servable(api, change):
    client, state, _ = api
    client.post(PATH, json=body(state))
    if change == "bundle":
        state.row.bundle["bundle"]["title"] = "원본과 다른 제목"
    elif change == "receipt":
        state.row.input_revision = "f" * 64
    else:
        state.walk.client_session_id = uuid4()
    read = client.get(PATH + QUERY).json()
    assert read["status"] == "failed" and read["bundle"] is None
    assert read["error_code"] == "invalid_stored_diary"


def test_legacy_exact_board_is_upgraded_without_generation(api):
    client, state, _ = api
    first = client.post(PATH, json=body(state)).json()
    state.row.bundle = deepcopy(first["bundle"])
    assert client.get(PATH + QUERY).json() == first
    assert state.row.bundle["format"] == "walk-diary-storage-v1"
    state.envelope = {**state.envelope, "id": str(uuid4())}
    assert client.get(PATH + QUERY).json() == {**first, "background_update_available": True}
    state.writer.assert_awaited_once()


def test_legacy_already_stale_board_is_not_upgraded_using_current_source(api):
    client, state, _ = api
    first = client.post(PATH, json=body(state)).json()
    state.row.bundle = deepcopy(first["bundle"])
    state.envelope = {**state.envelope, "id": str(uuid4())}
    read = client.get(PATH + QUERY).json()
    assert read["status"] == "stale" and read["bundle"] is None
    assert state.row.bundle == first["bundle"]
    state.writer.assert_awaited_once()
