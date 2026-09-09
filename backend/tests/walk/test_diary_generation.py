"""Authenticated diary lifecycle with real stored-format adapters and a fake provider/DB boundary."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daengs_backend.config import settings
from daengs_backend.core.database import get_session
from daengs_backend.core.deps import AppPrincipal, CurrentAppUser
from daengs_backend.routers import walk_storyboard as router
from daengs_backend.schemas.walk_storyboard import StoryboardRequest
from daengs_backend.services import walk_diary_input as reader
from daengs_backend.services import walk_diary_writing as writer
from daengs_backend.services import walk_storyboard as legacy
from daengs_walk.diary_input import digest
from tests.walk.test_diary_observations import varied_route
from tests.walk.test_diary_stamps import place_payload
from tests.walk.test_diary_writing import prose
from tests.walk.test_walk_photo_input import ENTRY, OWNER, WALK, context_envelope, entry
from tests.walk.test_walk_photo_input import row as photos

PATH = f"/app/walks/{WALK}/storyboard"
FORMAT = "walk-diary-bundle-v1"
QUERY = f"?bundle_format={FORMAT}&target_scene_count=3"


@pytest.fixture
def api(monkeypatch):
    walk, analysis, _ = varied_route()
    envelope = context_envelope()
    payload = place_payload(("registered-place", "합성 카페", 60))
    envelope.update(status="known", payload=payload, payload_sha256=digest(payload))
    state = SimpleNamespace(
        row=None,
        walk=walk,
        analysis=analysis,
        entries=[entry()],
        photo=None,
        envelope=envelope,
        before_write=None,
    )
    state.entries[0].mutation_id = uuid.uuid4()
    db = SimpleNamespace(
        new=set(),
        dirty=set(),
        deleted=set(),
        expire_all=Mock(),
        commit=AsyncMock(),
        add=lambda row: setattr(state, "row", row),
    )
    monkeypatch.setattr(
        reader.walks,
        "get_owned_for_update",
        AsyncMock(side_effect=lambda s, o, w: state.walk if o == OWNER and w == WALK else None),
    )
    monkeypatch.setattr(
        reader.storyboards, "latest_analysis", AsyncMock(side_effect=lambda *a: state.analysis)
    )
    monkeypatch.setattr(reader.entries, "entries", AsyncMock(side_effect=lambda *a: state.entries))
    monkeypatch.setattr(reader.photos, "current", AsyncMock(side_effect=lambda *a: state.photo))
    monkeypatch.setattr(
        reader.contexts,
        "current",
        AsyncMock(
            side_effect=lambda *a, **kw: (
                [],
                {"space.facility": SimpleNamespace(envelope=state.envelope)},
            )
        ),
    )
    monkeypatch.setattr(legacy.repo, "current", AsyncMock(side_effect=lambda *a: state.row))
    monkeypatch.setattr(legacy.repo, "reference_walks", AsyncMock(return_value=[]))
    monkeypatch.setattr(settings, "walk_diary_enabled", True)
    monkeypatch.setattr(settings, "walk_entry_v2_enabled", False)
    monkeypatch.setattr(settings, "walk_photo_metadata_enabled", True)
    monkeypatch.setattr(settings, "walk_entry_context_enabled", True)

    async def generate(payload, schema):
        # The persisted reservation is visible before external work begins.
        assert db.commit.await_count >= 1 and state.row.status == "running"
        if state.before_write:
            await state.before_write()
        return prose(payload)

    state.provider = AsyncMock(side_effect=generate)

    async def write(source, prepared):
        return await writer.write_diary(source, prepared, state.provider)

    state.writer = AsyncMock(side_effect=write)
    app = FastAPI()
    app.include_router(router.router)
    app.dependency_overrides[get_session] = lambda: db
    app.dependency_overrides[CurrentAppUser.__metadata__[0].dependency] = lambda: AppPrincipal(
        app_user_id=OWNER
    )
    app.dependency_overrides[router.get_diary_writer] = lambda: state.writer
    state.lookup = AsyncMock(return_value={})
    app.dependency_overrides[router.get_context_lookup] = lambda: state.lookup
    app.dependency_overrides[router.get_title_generator] = lambda: AsyncMock(
        side_effect=lambda bundle: bundle
    )
    return TestClient(app), state, db


def body(state, **updates):
    return {
        "bundle_format": FORMAT,
        "target_scene_count": 3,
        "expected_entries": {str(e.id): e.revision for e in state.entries},
        "expected_photo_manifest": None
        if state.photo is None
        else {"publisher_id": str(state.photo.publisher_id), "revision": state.photo.revision},
        **updates,
    }


def test_http_reserves_writes_stores_and_reuses_without_old_generator(api):
    client, state, _ = api
    pending = client.get(PATH + QUERY).json()
    assert pending["format"] == "walk-diary-response-v1" and pending["status"] == "pending"
    first = client.post(PATH, json=body(state))
    assert first.status_code == 200, first.text
    result = first.json()
    assert result["status"] == "ready" and result["bundle"]["format"] == FORMAT
    assert result["bundle"]["model_status"] == "accepted"
    assert result["preparation_counts"]["user_records"] == 1
    assert state.row.bundle == result["bundle"]
    original = result["bundle"]["scenes"][0]
    assert original["user_record"]["text"] == state.entries[0].payload["note"]
    assert original["anchor"]["point"] == {"lat": 37.5, "lng": 127.0}
    assert client.get(PATH + QUERY).json() == result
    assert client.post(PATH, json=body(state)).json() == result
    state.provider.assert_awaited_once()
    state.lookup.assert_not_awaited()
    assert client.post(PATH, json=body(state, refresh=True)).json()["generation"] == 2


def test_diary_and_legacy_share_a_row_but_never_return_each_others_bundle(api):
    client, state, _ = api
    legacy_body = {"expected_entries": {str(ENTRY): 2}}
    old = client.post(PATH, json=legacy_body).json()
    assert old["status"] == "ready" and old["bundle"]["format"].startswith("walk-storyboard")
    assert client.get(PATH + QUERY).json()["status"] == "stale"
    diary = client.post(PATH, json=body(state)).json()
    assert diary["generation"] == 2 and diary["bundle"]["format"] == FORMAT
    assert client.get(PATH).json()["status"] == "stale"
    assert client.get(PATH).json()["bundle"] is None
    assert client.post(PATH, json=legacy_body).json()["generation"] == 3
    assert client.get(PATH + QUERY).json()["bundle"] is None


@pytest.mark.parametrize("change", ["entry", "photo", "analysis", "background"])
def test_input_changes_during_writing_cannot_publish(api, change):
    client, state, _ = api

    async def alter():
        if change == "entry":
            state.entries[0].revision += 1
            state.entries[0].payload["note"] = "수정된 원문"
        elif change == "photo":
            state.photo = photos()
        elif change == "analysis":
            state.analysis.id = uuid.uuid4()
        else:
            state.envelope = {**state.envelope, "id": str(uuid.uuid4())}

    state.before_write = alter
    result = client.post(PATH, json=body(state)).json()
    assert result["status"] == "stale" and result["bundle"] is None
    assert state.row.status == "running" and state.row.bundle is None


def test_generation_takeover_and_running_cache_cannot_be_overwritten(api):
    client, state, _ = api

    async def newer():
        state.row.generation += 1
        state.row.status = "failed"
        state.row.error_code = "newer_generation"

    state.before_write = newer
    result = client.post(PATH, json=body(state)).json()
    assert result["generation"] == 2 and result["error_code"] == "newer_generation"
    assert state.row.bundle is None
    state.row.status, state.row.updated_at = "running", datetime.now(UTC)
    state.provider.reset_mock()
    assert client.post(PATH, json=body(state)).json()["status"] == "running"
    state.provider.assert_not_awaited()
    state.row.updated_at -= timedelta(seconds=61)
    state.before_write = None
    assert client.post(PATH, json=body(state)).json()["generation"] == 3


def test_photo_approval_and_entry_revision_checked_before_spending(api):
    client, state, _ = api
    state.photo = photos()
    for request in [
        body(state, expected_entries={}),
        body(state, expected_photo_manifest=None),
        body(
            state,
            expected_photo_manifest={"publisher_id": str(state.photo.publisher_id), "revision": 2},
        ),
    ]:
        assert client.post(PATH, json=request).status_code == 409
    state.writer.assert_not_awaited()
    result = client.post(PATH, json=body(state)).json()
    assert result["status"] == "ready" and result["photos_status"] == "complete"
    assert any(
        s["user_record"] and s["user_record"]["kind"] == "photo" for s in result["bundle"]["scenes"]
    )


def test_opt_in_ownership_validation_and_explicit_target(api, monkeypatch):
    client, state, _ = api
    assert client.get("/app/walks/storyboard/capabilities").json()["diary_formats"] == [FORMAT]
    missing = body(state)
    missing.pop("target_scene_count")
    assert client.post(PATH, json=missing).status_code == 422
    assert client.get(PATH + QUERY.replace("=3", "=51")).status_code == 422
    assert (
        client.post(
            PATH, json=body(state, bundle_format="walk-storyboard-candidates-v1")
        ).status_code
        == 422
    )
    assert (
        client.post(PATH.replace(str(WALK), str(uuid.uuid4())), json=body(state)).status_code == 404
    )
    monkeypatch.setattr(settings, "walk_diary_enabled", False)
    assert client.get("/app/walks/storyboard/capabilities").json()["diary_formats"] == []
    assert client.post(PATH, json=body(state)).status_code == 404
    state.writer.assert_not_awaited()


def test_empty_observation_only_and_unavailable_sources_keep_structure(api):
    client, state, _ = api
    state.entries = []
    result = client.post(PATH, json=body(state)).json()
    assert result["status"] == "ready" and result["preparation_counts"]["supplemented"] == 3
    assert result["bundle"]["model_status"] == "not_requested"
    state.provider.assert_not_awaited()
    state.analysis = None
    empty = client.post(PATH, json=body(state)).json()
    assert empty["status"] == "ready" and empty["bundle"]["scenes"] == []
    assert empty["preparation_counts"]["remaining_deficit"] == 3


def test_writer_failure_keeps_cards_and_requires_refresh_to_retry(api):
    client, state, _ = api
    state.provider.side_effect = ValueError("private provider error")
    failed = client.post(PATH, json=body(state)).json()
    assert failed["status"] == "ready" and failed["bundle"]["model_status"] == "unavailable"
    assert failed["bundle"]["failure_code"] == "provider_failed" and failed["bundle"]["scenes"]
    assert "private provider" not in str(failed)
    assert client.post(PATH, json=body(state)).json() == failed
    state.provider.assert_awaited_once()


async def test_request_cancellation_leaves_recoverable_lease(api):
    _, state, db = api
    state.writer.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await legacy.generate(
            db,
            OWNER,
            WALK,
            StoryboardRequest.model_validate(body(state)),
            state.lookup,
            diary_writer=state.writer,
        )
    assert state.row.status == "running" and state.row.bundle is None
    assert db.commit.await_count == 1


def test_selection_target_and_writer_policy_invalidate_cached_diary(api, monkeypatch):
    client, state, _ = api
    first = client.post(PATH, json=body(state)).json()
    assert client.get(PATH + QUERY.replace("=3", "=4")).json()["status"] == "stale"
    monkeypatch.setattr(writer, "MODEL", "test-new-policy-model")
    assert client.get(PATH + QUERY).json()["status"] == "stale"
    later = client.post(PATH, json=body(state)).json()
    assert later["generation"] == first["generation"] + 1
