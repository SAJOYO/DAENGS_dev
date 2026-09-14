"""Negotiated board API uses the existing reservation, private JSONB and old writer."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from daengs_backend.config import settings
from daengs_backend.routers import walk_storyboard as router
from daengs_backend.schemas.walk_storyboard import StoryboardRequest
from daengs_backend.services import walk_diary_generation as generation
from daengs_backend.services import walk_diary_input as reader
from daengs_backend.services import walk_diary_slot_writing as writer
from daengs_backend.services import walk_diary_writing as bundle_writer
from daengs_backend.services.walk_diary_board_storage import StoredBoard
from daengs_backend.services.walk_diary_generation import generate_diary
from daengs_walk.diary_board_output import BOARD_FORMAT, BOARD_RESPONSE, PublishedBoard
from tests.walk.support.diary_generation import FORMAT, PATH, body
from tests.walk.support.observations import stored, uploaded
from tests.walk.support.photo_input import OWNER, WALK

QUERY = f"?bundle_format={BOARD_FORMAT}&target_scene_count=3"


def request(state, **updates):
    return body(state, bundle_format=BOARD_FORMAT, **updates)


@pytest.fixture
def clock(monkeypatch):
    value = SimpleNamespace(now=datetime(2026, 9, 10, 12, tzinfo=UTC))
    monkeypatch.setattr(generation, "datetime", SimpleNamespace(now=lambda _tz: value.now))
    return value


@pytest.fixture
def legacy_context_wait(monkeypatch):
    # Only historical wait tests opt in. Normal HTTP tests use the default service.
    monkeypatch.setattr(
        generation, "generate_diary", partial(generate_diary, legacy_context_wait=True)
    )


@pytest.mark.parametrize("job_state", ["pending", "running"])
@pytest.mark.usefixtures("legacy_context_wait")
def test_legacy_board_waits_without_reservation_then_uses_completed_context(api, clock, job_state):
    client, state, db = api
    state.walk.created_at = clock.now  # The recorded walk happened yesterday, before this upload.
    background, state.envelope = state.envelope, None
    job = SimpleNamespace(state=job_state)
    state.context_jobs[state.entries[0].id] = [job]

    for response in (
        client.get(PATH + QUERY),
        client.post(PATH, json=request(state)),
        client.post(PATH, json=request(state, refresh=True)),
    ):
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["status"] == "pending" and result["generation"] == 0
        assert result["bundle"] is None
    assert state.row is None
    state.writer.assert_not_awaited()
    state.provider.assert_not_awaited()
    assert db.commit.await_count == 3  # Release each read transaction while collection is pending.

    job.state, state.envelope = "completed", background
    result = client.post(PATH, json=request(state)).json()
    assert result["status"] == "ready" and result["generation"] == 1
    scene = next(scene for scene in result["bundle"]["scenes"] if scene["kind"] == "user_record")
    assert "등록된 카페" in scene["body"]
    assert scene["body"].endswith(state.entries[0].payload["note"])
    state.writer.assert_awaited_once()
    state.provider.assert_awaited_once()


@pytest.mark.usefixtures("legacy_context_wait")
def test_legacy_wait_ends_exactly_ten_minutes_after_server_upload(api, clock):
    client, state, _ = api
    uploaded_at = clock.now
    state.walk.created_at = uploaded_at
    state.envelope = None
    state.context_jobs[state.entries[0].id] = [SimpleNamespace(state="running")]
    # A small DB/API clock difference and repeated retries cannot spend a generation early.
    for elapsed in (timedelta(seconds=-2), timedelta(minutes=10, microseconds=-1)):
        clock.now = uploaded_at + elapsed
        result = client.post(PATH, json=request(state)).json()
        assert result["status"] == "pending" and result["generation"] == 0
        assert state.row is None
        state.writer.assert_not_awaited()
    clock.now = uploaded_at + timedelta(minutes=10)
    result = client.post(PATH, json=request(state)).json()
    assert result["status"] == "ready" and result["generation"] == 1
    assert state.context_jobs[state.entries[0].id][0].state == "running"
    scene = next(scene for scene in result["bundle"]["scenes"] if scene["kind"] == "user_record")
    assert scene["body"].endswith(state.entries[0].payload["note"])
    assert "등록된 카페" not in scene["body"]
    state.writer.assert_awaited_once()


@pytest.mark.parametrize(
    ("enabled", "job_states"),
    [
        (True, []),
        (True, ["completed"]),
        (True, ["failed"]),
        (True, ["cancelled"]),
        (False, ["pending"]),
    ],
    ids=["no-jobs", "completed", "failed", "cancelled", "disabled"],
)
@pytest.mark.usefixtures("legacy_context_wait")
def test_legacy_board_does_not_wait_without_active_collection(
    api, clock, monkeypatch, enabled, job_states
):
    client, state, _ = api
    state.walk.created_at = clock.now
    state.envelope = None
    state.context_jobs[state.entries[0].id] = [SimpleNamespace(state=value) for value in job_states]
    monkeypatch.setattr(settings, "walk_entry_context_enabled", enabled)
    result = client.post(PATH, json=request(state)).json()
    assert result["status"] == "ready" and result["generation"] == 1
    state.writer.assert_awaited_once()


@pytest.mark.parametrize(
    "uploaded_at", [None, datetime(2026, 9, 10, 12, tzinfo=UTC).replace(tzinfo=None)]
)
@pytest.mark.usefixtures("legacy_context_wait")
def test_unknown_server_upload_time_does_not_block_legacy_board(api, clock, uploaded_at):
    client, state, _ = api
    state.walk.created_at = uploaded_at
    state.envelope = None
    state.context_jobs[state.entries[0].id] = [SimpleNamespace(state="pending")]
    result = client.post(PATH, json=request(state)).json()
    assert result["status"] == "ready" and result["generation"] == 1
    state.writer.assert_awaited_once()


def test_published_board_is_preserved_when_current_context_is_pending(api, clock):
    client, state, _ = api
    state.walk.created_at = clock.now
    first = client.post(PATH, json=request(state)).json()
    saved = deepcopy(state.row.bundle)
    state.envelope = None
    state.context_jobs[state.entries[0].id] = [SimpleNamespace(state="pending")]
    for response in (
        client.get(PATH + QUERY),
        client.post(PATH, json=request(state, refresh=True)),
    ):
        result = response.json()
        assert result["status"] == "ready" and result["generation"] == 1
        assert result["bundle"] == first["bundle"]
    assert state.row.bundle == saved
    state.writer.assert_awaited_once()


@pytest.mark.usefixtures("legacy_context_wait")
def test_deleted_entry_collection_does_not_delay_legacy_board(api, clock):
    client, state, _ = api
    state.walk.created_at = clock.now
    state.envelope = None
    state.entries[0].payload = None
    state.context_jobs[state.entries[0].id] = [SimpleNamespace(state="pending")]
    result = client.post(PATH, json=request(state)).json()
    assert result["status"] == "ready" and result["generation"] == 1
    assert all(scene["kind"] != "user_record" for scene in result["bundle"]["scenes"])
    reader.contexts.current.assert_not_awaited()


def test_fixed_board_uses_slot_writer_and_hides_private_input(api):
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
    assert state.row.bundle["format"] == "walk-diary-board-storage-v2"
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
    state.provider.assert_awaited_once()  # Ordinary path shape is usable without speed outliers.


def test_saved_v1_is_read_without_conversion_or_generation(api):
    client, state, _ = api
    first = client.post(PATH, json=body(state)).json()
    saved = deepcopy(state.row.bundle)
    # New client target differs; the saved policy is retained with the saved v1 board.
    assert client.get(PATH + QUERY.replace("=3", "=5")).json() == first
    assert client.post(PATH, json=request(state, refresh=True)).json() == first
    assert state.row.bundle == saved
    state.provider.assert_awaited_once()


@pytest.mark.parametrize("requested_format", [BOARD_FORMAT, FORMAT])
@pytest.mark.parametrize("edit_source", [False, True])
def test_default_router_uses_saved_bundle_writer_after_negotiation(
    api, monkeypatch, requested_format, edit_source
):
    client, state, _ = api
    client.app.dependency_overrides.pop(router.get_diary_writer)
    # The model callable is bound as a Python default. Replace only that external
    # boundary; keep the router, negotiation, preparation and both writers real.
    monkeypatch.setattr(bundle_writer.write_diary, "__defaults__", (state.provider,))
    first_response = client.post(PATH, json=body(state))
    assert first_response.status_code == 200, first_response.text
    first = first_response.json()
    assert first["status"] == "ready" and first["bundle"]["model_status"] == "accepted"
    saved = deepcopy(state.row.bundle)
    if edit_source:
        state.entries[0].revision += 1
        state.entries[0].payload["note"] = "  수정한 원문\n그대로 보존  "
        state.envelope["target"]["revision"] = state.entries[0].revision

    response = client.post(PATH, json=body(state, bundle_format=requested_format))
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["status"] == "ready", value
    assert value["format"] == "walk-diary-response-v1"
    assert value["bundle"]["format"] == FORMAT
    assert value["bundle"]["model_status"] == "accepted"
    assert value["generation"] == first["generation"] + int(edit_source)
    assert value["entry_revisions"] == {str(e.id): e.revision for e in state.entries}
    assert state.row.bundle["format"] == "walk-diary-storage-v1"
    assert state.row.bundle["bundle"] == value["bundle"]
    if edit_source:
        assert value["input_revision"] != first["input_revision"]
        original = next(s["user_record"] for s in value["bundle"]["scenes"] if s["user_record"])
        assert original["text"] == state.entries[0].payload["note"]
    else:
        assert value == first and state.row.bundle == saved
    assert state.provider.await_count == 1 + int(edit_source)
    for call in state.provider.await_args_list:
        payload, schema = call.args
        assert "background_dictionary" in payload and "scenes" in payload
        assert isinstance(schema, dict)
    state.writer.assert_not_awaited()
    state.lookup.assert_not_awaited()


def test_saved_legacy_is_returned_in_its_original_format(api):
    client, state, _ = api
    client.app.dependency_overrides.pop(router.get_diary_writer)
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
