"""Synthetic metadata transport and real stored-format adapters, no provider calls."""

import uuid
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daengs_backend.config import settings
from daengs_backend.core.database import get_session
from daengs_backend.core.deps import AppPrincipal, CurrentAppUser
from daengs_backend.routers import walk_photo as router
from daengs_backend.schemas.walk_photo import PhotoManifestWrite
from daengs_backend.services import walk_diary_input as adapter
from daengs_backend.services import walk_photo as service
from daengs_walk.diary_input import digest

OWNER, WALK, SESSION, PUBLISHER, PHOTO, ENTRY = [uuid.uuid4() for _ in range(6)]
AT = datetime(2026, 9, 9, tzinfo=UTC)


def walk():
    return SimpleNamespace(
        id=WALK,
        app_user_id=OWNER,
        client_session_id=SESSION,
        pet_ids=[],
        started_at=AT,
        ended_at=AT + timedelta(hours=1),
    )


def request(revision=1, expected=0, *, include=True):
    return PhotoManifestWrite.model_validate(
        {
            "publisher_id": str(PUBLISHER),
            "revision": revision,
            "expected_revision": expected,
            "photos": [
                {
                    "id": str(PHOTO),
                    "captured_at": (AT + timedelta(minutes=5)).isoformat(),
                    "location_captured_at": (AT + timedelta(minutes=5, seconds=-2)).isoformat(),
                    "point": {"lat": 37.5, "lng": 127.0},
                    "accuracy_m": 5.0,
                }
            ]
            if include
            else [],
        }
    )


def row(spec=None):
    spec = spec or request()
    records, request_hash = service.transition(None, spec, walk())
    return SimpleNamespace(
        publisher_id=PUBLISHER, revision=spec.revision, request_hash=request_hash, records=records
    )


def entry():
    return SimpleNamespace(
        id=ENTRY,
        revision=2,
        payload={
            "kind": "note",
            "note": "  있는 그대로\n  ",
            "recorded_at": AT.isoformat(),
            "location": {
                "lat": 37.5,
                "lng": 127.0,
                "captured_at": AT.isoformat(),
                "accuracy_m": 5.0,
            },
        },
    )


def test_lost_ack_retry_is_idempotent_and_old_or_conflicting_writes_fail():
    stored = row()
    assert service.transition(stored, request(), walk())[0] == stored.records
    for spec in [
        request(2, 0),
        request(1, 0, include=False),
        request().model_copy(update={"publisher_id": uuid.uuid4()}),
    ]:
        with pytest.raises(service.PhotoConflict):
            service.transition(stored, spec, walk())


def test_delete_scrubs_metadata_retains_tombstone_and_rejects_resurrection():
    stored = row()
    removed, request_hash = service.transition(stored, request(2, 1, include=False), walk())
    assert removed == [{"id": str(PHOTO), "revision": 2, "content": None}]
    stored.records, stored.revision, stored.request_hash = removed, 2, request_hash
    with pytest.raises(service.PhotoConflict, match="삭제"):
        service.transition(stored, request(3, 2), walk())


def test_photo_zero_and_not_received_are_distinct():
    assert service.response(walk(), None).status == "not_available"
    assert service.response(walk(), row(request(include=False))).status == "complete"
    assert (
        adapter.assemble_input(walk(), None, [], [], None, []).source.photos_status
        == "not_available"
    )


@pytest.mark.parametrize("change", ["future_location", "duplicate", "file_path", "nan"])
def test_transport_rejects_invalid_metadata(change):
    raw = request().model_dump(mode="json")
    if change == "future_location":
        raw["photos"][0]["location_captured_at"] = (AT + timedelta(hours=1)).isoformat()
    elif change == "duplicate":
        raw["photos"].append(deepcopy(raw["photos"][0]))
    elif change == "file_path":
        raw["photos"][0]["file_path"] = "/private/photo.jpg"
    else:
        raw["photos"][0]["accuracy_m"] = float("nan")
    with pytest.raises(ValueError):
        PhotoManifestWrite.model_validate(raw)


def test_transport_rejects_shutter_outside_walk():
    spec = request()
    short = walk()
    short.ended_at = AT + timedelta(minutes=1)
    with pytest.raises(service.PhotoConflict, match="촬영"):
        service.transition(None, spec, short)


def test_input_preserves_photo_note_and_route_fingerprint_without_computing_motion():
    analysis = SimpleNamespace(
        id=uuid.uuid4(), input_fingerprint="sha256:" + "a" * 64, calculation_version=1
    )
    result = adapter.assemble_input(walk(), analysis, [entry()], [], row(), [])
    source = result.source
    note, photo = source.records
    assert note.content.text == "  있는 그대로\n  "
    assert photo.anchor.location_at < photo.anchor.event_at
    assert photo.anchor.accuracy_m == 5
    assert photo.content.media_ref == f"app-private-photo:{PHOTO}"
    assert source.route.input_fingerprint == "a" * 64
    assert source.observations == () and source.evidence_origin == "unknown"
    assert source.photos_status == "complete"
    assert source.photo_manifest.publisher_id == str(PUBLISHER)
    assert source.photo_manifest.revision == 1


def test_v2_unlocated_and_provisional_pin_metadata_are_preserved():
    original = entry()
    original.payload.update(kind="behavior", behavior_code="sniffing", note=None, location=None)
    base = {
        "resolution_id": str(uuid.uuid4()),
        "target_at": AT.isoformat(),
        "point": None,
        "method": "none",
        "computed_at": AT.isoformat(),
        "resolve_by": (AT + timedelta(seconds=30)).isoformat(),
        "policy_version": "walk-action-pin-v1",
        "algorithm_version": "test-v1",
        "source_refs": [],
        "uncertainty_m": None,
        "uncertainty_basis": "unknown",
    }
    for state, reason in [("provisional", "awaiting_observations"), ("unlocated", "no_evidence")]:
        pin = {**base, "state": state, "reason": reason}
        result = adapter.entry_record(original, SimpleNamespace(pin_revision=4, payload=pin))
        assert result.ref.pin_revision == 4
        assert result.content.code == "sniffing"
        assert result.anchor.point is None and result.anchor.position_state == state
        assert result.pin_payload == pin


def test_context_hash_and_target_are_checked_before_reusing_saved_provider_data():
    original = entry()
    payload = {"items": [], "radius_m": 250}
    envelope = {
        "id": str(uuid.uuid4()),
        "schema_version": "walk-entry-context-v1",
        "target": {
            "store": "walk_entry",
            "walk_id": str(WALK),
            "id": str(ENTRY),
            "revision": 2,
            "event_at": AT.isoformat(),
            "location": original.payload["location"],
        },
        "tags": ["space.facility"],
        "status": "empty",
        "reason": None,
        "provenance": {
            "provider": "place-search",
            "policy_version": "walk-entry-context-v1",
            "retrieved_at": AT.isoformat(),
            "temporal_basis": "lookup_snapshot",
        },
        "payload": payload,
        "payload_sha256": digest(payload),
    }
    result = adapter.assemble_input(walk(), None, [original], [], None, [envelope])
    assert len(result.source.backgrounds) == 1
    assert result.source.selected_background_ids == ()  # The selector is the next unit.
    envelope["payload"]["radius_m"] = 500
    result = adapter.assemble_input(walk(), None, [original], [], None, [envelope])
    assert not result.source.backgrounds and len(result.excluded_backgrounds) == 1


def test_photo_removal_changes_input_revision_and_keeps_deleted_reference():
    stored = row()
    before = adapter.assemble_input(walk(), None, [], [], stored, []).source
    stored.records, _ = service.transition(stored, request(2, 1, include=False), walk())
    after = adapter.assemble_input(walk(), None, [], [], stored, []).source
    assert before.revision() != after.revision()
    assert after.records[0].deleted and after.records[0].anchor is None


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(settings, "walk_photo_metadata_enabled", True)
    state = {"row": None}

    async def owned(db, owner, walk_id, *, lock):
        assert lock
        return walk() if owner == OWNER and walk_id == WALK else None

    async def current(db, walk_id):
        return state["row"]

    monkeypatch.setattr(service.walks, "owned_walk", owned)
    monkeypatch.setattr(service.repo, "current", current)
    db = SimpleNamespace(commit=AsyncMock(), add=lambda value: state.update(row=value))
    app = FastAPI()
    app.include_router(router.router)
    app.dependency_overrides[get_session] = lambda: db
    app.dependency_overrides[CurrentAppUser.__metadata__[0].dependency] = lambda: AppPrincipal(
        app_user_id=OWNER
    )
    with TestClient(app) as client:
        yield client, db, state


def test_authenticated_transport_roundtrip_and_wrong_walk_is_404(api):
    client, _db, _ = api
    path = f"/app/walks/{WALK}/photo-metadata"
    assert client.get(path).json()["status"] == "not_available"
    first = client.put(path, json=request().model_dump(mode="json"))
    assert first.status_code == 200 and first.json()["revision"] == 1
    assert client.get(path).json() == first.json()
    assert client.put(path, json=request().model_dump(mode="json")).json() == first.json()
    assert (
        client.put(
            f"/app/walks/{uuid.uuid4()}/photo-metadata", json=request().model_dump(mode="json")
        ).status_code
        == 404
    )
    client.app.dependency_overrides[CurrentAppUser.__metadata__[0].dependency] = lambda: (
        AppPrincipal(app_user_id=uuid.uuid4())
    )
    assert client.get(path).status_code == 404


def test_disabled_capability_and_endpoint_never_access_optional_table(api, monkeypatch):
    client, _, _ = api
    monkeypatch.setattr(settings, "walk_photo_metadata_enabled", False)
    blocked = AsyncMock(side_effect=AssertionError("optional table was read"))
    monkeypatch.setattr(service.repo, "current", blocked)
    assert client.get("/app/walks/photo-metadata/capabilities").json()["write_versions"] == []
    assert client.get(f"/app/walks/{WALK}/photo-metadata").status_code == 404
    blocked.assert_not_called()


async def test_input_reader_locks_owner_and_expires_prior_generation_identity_map(monkeypatch):
    session = SimpleNamespace(new=set(), dirty=set(), deleted=set(), expire_all=Mock())
    owned = AsyncMock(return_value=walk())
    monkeypatch.setattr(adapter.entries, "owned_walk", owned)
    monkeypatch.setattr(adapter.entries, "entries", AsyncMock(return_value=[entry()]))
    monkeypatch.setattr(adapter.storyboards, "latest_analysis", AsyncMock(return_value=None))
    monkeypatch.setattr(settings, "walk_entry_v2_enabled", False)
    monkeypatch.setattr(settings, "walk_entry_context_enabled", False)
    monkeypatch.setattr(settings, "walk_photo_metadata_enabled", False)
    missing_table = AsyncMock(side_effect=AssertionError("optional table access"))
    monkeypatch.setattr(adapter.photos, "current", missing_table)
    result = await adapter.read_input(session, OWNER, WALK)
    owned.assert_awaited_once_with(session, OWNER, WALK, lock=True)
    session.expire_all.assert_called_once()
    missing_table.assert_not_called()
    assert result.source.records[0].content.text == "  있는 그대로\n  "
    assert result.source.photos_status == "not_available"


async def test_input_reader_does_not_read_private_sources_for_another_owner(monkeypatch):
    session = SimpleNamespace(new=set(), dirty=set(), deleted=set(), expire_all=Mock())
    monkeypatch.setattr(adapter.entries, "owned_walk", AsyncMock(return_value=None))
    private = AsyncMock(side_effect=AssertionError("private records read"))
    monkeypatch.setattr(adapter.entries, "entries", private)
    with pytest.raises(LookupError):
        await adapter.read_input(session, uuid.uuid4(), WALK)
    private.assert_not_called()
