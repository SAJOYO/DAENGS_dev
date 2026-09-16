"""Synthetic metadata transport and real stored-format adapters, no provider calls."""

import uuid
from copy import deepcopy
from datetime import timedelta
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
from daengs_backend.services.walk_diary.preparation import input as adapter
from daengs_backend.services.walk_photos import api as service
from daengs_backend.services.walk_records.v2 import legacy_pin
from tests.walk.support.photo_input import (
    AT,
    OWNER,
    PHOTO,
    PUBLISHER,
    WALK,
    context_envelope,
    entry,
    pin_context,
    request,
    row,
    walk,
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
    with pytest.raises(service.PhotoInvalid, match="촬영"):
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


def legacy_behavior():
    original = entry()
    original.payload.update(kind="behavior", behavior_code="sniffing", note=None)
    original.payload["location"]["captured_at"] = (AT - timedelta(seconds=5)).isoformat()
    sidecar = SimpleNamespace(entry_id=original.id, pin_revision=0, payload=legacy_pin(original))
    return original, sidecar


def test_v1_behavior_edited_via_v2_keeps_original_sample_time_and_accuracy():
    original, sidecar = legacy_behavior()
    result = adapter.assemble_input(walk(), None, [original], [sidecar], None, [])
    record = result.source.records[0]
    assert record.content.code == "sniffing"
    assert record.ref.pin_revision == 0 and record.pin_payload == sidecar.payload
    assert record.anchor == adapter.entry_anchor(AT, original.payload["location"])
    assert record.anchor.method == "last_known" and record.anchor.position_state == "legacy"
    assert record.anchor.location_at == AT - timedelta(seconds=5)
    assert record.anchor.accuracy_m == 5 and record.anchor.source_fixes == ()


@pytest.mark.parametrize("change", ["point", "time", "refs", "algorithm", "identity", "revision"])
def test_legacy_sidecar_must_still_match_its_original_record(change):
    original, sidecar = legacy_behavior()
    if change == "point":
        sidecar.payload["point"]["lat"] += 1
    elif change == "time":
        sidecar.payload["target_at"] = (AT + timedelta(seconds=1)).isoformat()
    elif change == "refs":
        sidecar.payload["source_refs"] = [{"client_seq": 1, "chain_index": 0, "at": AT.isoformat()}]
    elif change == "algorithm":
        sidecar.payload["algorithm_version"] = "walk-action-pin-v1"
    elif change == "identity":
        sidecar.payload["resolution_id"] = str(uuid.uuid4())
    else:
        sidecar.pin_revision = 1
    with pytest.raises(ValueError, match="legacy pin"):
        adapter.entry_record(original, sidecar)


def test_native_v2_pin_without_source_refs_is_still_rejected():
    original, sidecar = legacy_behavior()
    sidecar.payload.update(policy_version="walk-action-pin-v1", algorithm_version="native-v1")
    sidecar.pin_revision = 1
    with pytest.raises(ValueError, match="coordinates need source references"):
        adapter.entry_record(original, sidecar)


def test_legacy_v2_background_survives_only_with_its_exact_stored_pin_and_location():
    original, sidecar = legacy_behavior()
    envelope = context_envelope()
    envelope["schema_version"] = "walk-entry-context-v2"
    envelope["target"].update(
        pin=deepcopy(sidecar.payload),
        pin_revision=0,
        location=deepcopy(original.payload["location"]),
    )
    envelope["provenance"].update(policy_version="walk-entry-context-v2", location_basis="observed")
    assert (
        len(
            adapter.assemble_input(
                walk(), None, [original], [sidecar], None, [envelope]
            ).source.backgrounds
        )
        == 1
    )
    envelope["target"]["location"]["captured_at"] = AT.isoformat()
    result = adapter.assemble_input(walk(), None, [original], [sidecar], None, [envelope])
    assert not result.source.backgrounds and len(result.excluded_backgrounds) == 1
    envelope["target"]["location"] = deepcopy(original.payload["location"])
    envelope["target"]["pin"]["resolution_id"] = str(uuid.uuid4())
    result = adapter.assemble_input(walk(), None, [original], [sidecar], None, [envelope])
    assert not result.source.backgrounds and len(result.excluded_backgrounds) == 1


def test_context_hash_and_target_are_checked_before_reusing_saved_provider_data():
    original = entry()
    envelope = context_envelope()
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


def test_photo_validation_rejection_is_422_without_advancing_the_server_revision(api):
    client, db, state = api
    path = f"/app/walks/{WALK}/photo-metadata"
    assert client.put(path, json=request().model_dump(mode="json")).status_code == 200
    prior = deepcopy(state["row"].records)
    bad = request(2, 1).model_dump(mode="json")
    bad["photos"][0]["captured_at"] = (AT + timedelta(hours=2)).isoformat()
    db.commit.reset_mock()
    assert client.put(path, json=bad).status_code == 422
    db.commit.assert_not_awaited()
    assert state["row"].revision == 1 and state["row"].records == prior
    # A corrected later local revision can still use the last actual ACK.
    assert (
        client.put(path, json=request(3, 1, include=False).model_dump(mode="json")).status_code
        == 200
    )
    assert client.put(path, json=request(4, 1).model_dump(mode="json")).status_code == 409


def test_deleted_photo_history_limit_is_422_and_can_be_corrected(api):
    client, db, state = api
    stored = row()
    stored.records = [{"id": str(uuid.uuid4()), "revision": 2, "content": None} for _ in range(200)]
    state["row"] = stored
    path = f"/app/walks/{WALK}/photo-metadata"
    assert client.put(path, json=request(2, 1).model_dump(mode="json")).status_code == 422
    db.commit.assert_not_awaited()
    assert stored.revision == 1
    assert (
        client.put(path, json=request(3, 1, include=False).model_dump(mode="json")).status_code
        == 200
    )


async def test_input_reader_locks_owner_and_expires_prior_generation_identity_map(monkeypatch):
    session = SimpleNamespace(new=set(), dirty=set(), deleted=set(), expire_all=Mock())
    owned = AsyncMock(return_value=walk())
    monkeypatch.setattr(adapter.walks, "get_owned_for_update", owned)
    monkeypatch.setattr(adapter.entries, "entries", AsyncMock(return_value=[entry()]))
    monkeypatch.setattr(adapter.storyboards, "latest_analysis", AsyncMock(return_value=None))
    monkeypatch.setattr(settings, "walk_entry_v2_enabled", False)
    monkeypatch.setattr(settings, "walk_entry_context_enabled", False)
    monkeypatch.setattr(settings, "walk_photo_metadata_enabled", False)
    missing_table = AsyncMock(side_effect=AssertionError("optional table access"))
    monkeypatch.setattr(adapter.photos, "current", missing_table)
    result = await adapter.read_input(session, OWNER, WALK)
    owned.assert_awaited_once_with(session, OWNER, WALK)
    session.expire_all.assert_called_once()
    missing_table.assert_not_called()
    assert result.source.records[0].content.text == "  있는 그대로\n  "
    assert result.source.photos_status == "not_available"


async def test_input_reader_does_not_read_private_sources_for_another_owner(monkeypatch):
    session = SimpleNamespace(new=set(), dirty=set(), deleted=set(), expire_all=Mock())
    monkeypatch.setattr(adapter.walks, "get_owned_for_update", AsyncMock(return_value=None))
    private = AsyncMock(side_effect=AssertionError("private records read"))
    monkeypatch.setattr(adapter.entries, "entries", private)
    with pytest.raises(LookupError):
        await adapter.read_input(session, uuid.uuid4(), WALK)
    private.assert_not_called()


@pytest.mark.parametrize("change", [None, "revision", "pin", "method", "v1"])
def test_pin_context_uses_current_estimate_and_rejects_stale_sources(change):
    sidecar, envelope = pin_context()
    if change == "revision":
        envelope["target"]["pin_revision"] = 2
    elif change == "pin":
        envelope["target"]["pin"]["point"]["lng"] = 127.2
    elif change == "method":
        envelope["provenance"]["location_basis"] = "observed"
    elif change == "v1":
        envelope = context_envelope()
    result = adapter.assemble_input(walk(), None, [entry()], [sidecar], None, [envelope])
    if change:
        assert not result.source.backgrounds and len(result.excluded_backgrounds) == 1
    else:
        background = result.source.backgrounds[0]
        assert background.query_point.lat == 37.6  # Not the original location's 37.5.
        assert result.source.records[0].anchor.method == "estimated"
        assert background.payload_schema == "walk-entry-context-v2"


def test_v2_unlocated_pin_never_borrows_original_location_for_context():
    sidecar, envelope = pin_context()
    sidecar.payload.update(
        state="unlocated",
        method="none",
        point=None,
        source_refs=[],
        uncertainty_m=None,
        uncertainty_basis="unknown",
        reason="no_evidence",
    )
    envelope["target"]["pin"] = deepcopy(sidecar.payload)
    envelope["provenance"].update(
        location_basis="none", retrieved_at=None, temporal_basis="unknown"
    )
    envelope.update(status="not_requested", reason="no_location", payload=None, payload_sha256=None)
    result = adapter.assemble_input(walk(), None, [entry()], [sidecar], None, [envelope])
    assert len(result.source.backgrounds) == 1
    assert result.source.backgrounds[0].query_point is None
    assert result.source.records[0].anchor.point is None


async def test_input_reader_queries_the_saved_v2_policy_including_a_pinless_note(monkeypatch):
    session = SimpleNamespace(new=set(), dirty=set(), deleted=set(), expire_all=Mock())
    original = entry()
    sidecar, envelope = pin_context()
    sidecar.payload = None
    envelope["target"]["pin"] = None
    envelope["provenance"]["location_basis"] = "original_location"
    monkeypatch.setattr(adapter.walks, "get_owned_for_update", AsyncMock(return_value=walk()))
    monkeypatch.setattr(adapter.entries, "entries", AsyncMock(return_value=[original]))
    monkeypatch.setattr(adapter.pins, "pins", AsyncMock(return_value=[sidecar]))
    current = AsyncMock(return_value=([], {"space.facility": SimpleNamespace(envelope=envelope)}))
    monkeypatch.setattr(adapter.contexts, "current", current)
    monkeypatch.setattr(adapter.storyboards, "latest_analysis", AsyncMock(return_value=None))
    monkeypatch.setattr(settings, "walk_entry_v2_enabled", True)
    monkeypatch.setattr(settings, "walk_entry_context_enabled", True)
    monkeypatch.setattr(settings, "walk_photo_metadata_enabled", False)
    result = await adapter.read_input(session, OWNER, WALK)
    current.assert_awaited_once_with(session, original, policy="walk-entry-context-v2")
    assert len(result.source.backgrounds) == 1
    assert result.source.records[0].ref.pin_revision == 3
