"""Real schemas/router/service with an in-memory DAO; DB trigger/locking tests are separate."""

import copy
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from daengs_backend.config import settings
from daengs_backend.core.database import get_session, get_snapshot_session
from daengs_backend.core.deps import AppPrincipal, CurrentAppUser
from daengs_backend.models.walk_entry import WalkEntry
from daengs_backend.models.walk_entry_v2 import WalkEntryMutation, WalkEntryPin
from daengs_backend.routers import walk_entry, walk_entry_v2
from daengs_backend.schemas.walk_entry_v2 import EntryWriteV2, Pin
from daengs_backend.services.walk_records import v2 as service
from daengs_backend.services.walk_session.chunk import encode_chunk
from tests.walk.support.entry_v2 import (
    AT,
    ENTRY,
    OWNER,
    PET,
    WALK,
    body,
    completion,
    located,
    pin,
    raw_point,
)

PATH = f"/app/v2/walks/{WALK}/entries/{ENTRY}"
V1 = f"/app/walks/{WALK}/entries/{ENTRY}"


class Memory:
    def __init__(self):
        self.rows, self.pins, self.receipts = {}, {}, {}
        self.points = []
        self.commit = AsyncMock()

    def add(self, row):
        if isinstance(row, WalkEntry):
            self.rows[row.id] = row
        elif isinstance(row, WalkEntryPin):
            self.pins[row.entry_id] = row
        elif isinstance(row, WalkEntryMutation):
            self.receipts[(row.entry_id, row.mutation_id)] = row

    async def flush(self):
        # Mirror deletion only; the actual SQL trigger is exercised against PostgreSQL separately.
        for row in self.rows.values():
            if row.payload is None:
                if row.id in self.pins:
                    self.pins[row.id].payload = None
                    self.pins[row.id].pin_revision = 0
                self.receipts = {k: v for k, v in self.receipts.items() if k[0] != row.id}


@pytest.fixture
def api(monkeypatch):
    db = Memory()
    monkeypatch.setattr(settings, "walk_entry_v2_enabled", True)
    monkeypatch.setattr(settings, "walk_entry_v2_write_enabled", True)
    walk = SimpleNamespace(
        id=WALK,
        pet_ids=[PET],
        started_at=AT - timedelta(minutes=1),
        ended_at=AT + timedelta(minutes=1),
    )
    monkeypatch.setattr(
        service.entries,
        "owned_walk",
        AsyncMock(side_effect=lambda s, o, w, **kw: walk if o == OWNER and w == WALK else None),
    )
    monkeypatch.setattr(
        service.entries, "get_entry", AsyncMock(side_effect=lambda s, w, e: db.rows.get(e))
    )
    monkeypatch.setattr(
        service.entries, "entries", AsyncMock(side_effect=lambda s, ws: list(db.rows.values()))
    )
    monkeypatch.setattr(service.entries, "profile_walks", AsyncMock(return_value=[walk]))
    # 연결 안 된 아이는 자기 하나가 그룹입니다 (MVP 결정 §7).
    monkeypatch.setattr(service.entries, "pet_group_ids", AsyncMock(return_value=[PET]))
    monkeypatch.setattr(
        service.entries,
        "pet_is_accessible",
        AsyncMock(side_effect=lambda s, o, p: o == OWNER and p == PET),
    )
    monkeypatch.setattr(service.repo, "pin", AsyncMock(side_effect=lambda s, w, e: db.pins.get(e)))
    monkeypatch.setattr(
        service.repo, "pins", AsyncMock(side_effect=lambda s, ws: list(db.pins.values()))
    )
    monkeypatch.setattr(
        service.repo, "receipt", AsyncMock(side_effect=lambda s, w, e, m: db.receipts.get((e, m)))
    )
    monkeypatch.setattr(
        service.repo,
        "raw_chunks",
        AsyncMock(
            side_effect=lambda s, w: (
                [SimpleNamespace(payload=encode_chunk(db.points))] if db.points else []
            )
        ),
    )
    monkeypatch.setattr(
        service.repo,
        "contains_v2",
        AsyncMock(
            side_effect=lambda s, ws, entry_id=None: (
                entry_id in db.pins
                if entry_id
                else any(e in db.pins and r.payload is not None for e, r in db.rows.items())
            )
        ),
    )
    app = FastAPI()
    for router in (walk_entry.router, walk_entry_v2.router, walk_entry_v2.capabilities_router):
        app.include_router(router)
    app.dependency_overrides[CurrentAppUser.__metadata__[0].dependency] = lambda: AppPrincipal(
        app_user_id=OWNER
    )
    app.dependency_overrides[get_session] = lambda: db
    app.dependency_overrides[get_snapshot_session] = lambda: db
    return TestClient(app), db


def test_null_location_action_finalize_profile_and_minimal_delete(api):
    client, db = api
    request = body()
    created = client.put(PATH, json=request)
    assert created.status_code == 200, created.text
    assert created.json()["content"]["location"] is None
    before = client.post("/app/v2/walks/record-profile/query", json={"pet_id": str(PET)}).json()
    assert before["behaviors"]["sniffing"]["entry_count"] == 1
    finalized = client.put(PATH + "/pin", json=completion(request))
    assert finalized.status_code == 200, finalized.text
    assert finalized.json()["pin_revision"] == 2
    assert finalized.json()["pin"]["state"] == "unlocated"
    after = client.post("/app/v2/walks/record-profile/query", json={"pet_id": str(PET)}).json()
    assert before["source_revision"] != after["source_revision"]
    assert after["behaviors"] == before["behaviors"]
    result = client.delete(PATH, params={"expected_revision": 2, "mutation_id": str(uuid.uuid4())})
    assert result.status_code == 200, result.text
    assert set(result.json()) == {"id", "revision", "mutation_id", "deleted"}
    assert not db.receipts and db.pins[ENTRY].payload is None
    assert client.put(PATH, json=request).status_code == 410
    assert client.get(PATH.rsplit("/", 1)[0]).json()["entries"] == [result.json()]


def test_pause_cutoff_does_not_use_later_resumed_gps_to_reject_unlocated(api):
    client, db = api
    request = body()
    assert client.put(PATH, json=request).status_code == 200
    db.points = [raw_point(seconds=3).model_copy(update={"chain_index": 1})]
    final = completion(request)
    final["pin"].update(
        reason="session_ended", observation_cutoff_at=(AT + timedelta(seconds=1)).isoformat()
    )
    response = client.put(PATH + "/pin", json=final)
    assert response.status_code == 200, response.text
    assert response.json()["pin"]["state"] == "unlocated"
    assert client.get("/app/walks/entry-capabilities").json()["pin_observation_cutoff_supported"]


def test_optional_cutoff_keeps_pre_extension_receipt_hash(api):
    client, db = api
    request = body()
    old_payload = EntryWriteV2.model_validate(request).model_dump(mode="json")
    old_payload.pop("recording_evidence_fingerprint")  # Absent from the original wire contract.
    old_payload["pin"].pop("observation_cutoff_at")
    expected = service.digest({"operation": "content", "pin_supplied": True, **old_payload})
    assert client.put(PATH, json=request).status_code == 200
    assert next(iter(db.receipts.values())).request_hash == expected
    request["pin"]["observation_cutoff_at"] = None
    assert client.put(PATH, json=request).status_code == 200


def test_cutoff_cannot_hide_a_past_fix_or_include_post_cutoff_references(api):
    client, db = api
    request = body()
    assert client.put(PATH, json=request).status_code == 200
    db.points = [raw_point()]
    final = completion(request)
    final["pin"]["observation_cutoff_at"] = AT.isoformat()
    assert client.put(PATH + "/pin", json=final).status_code == 422
    invalid = completion(request, located_pin=True)["pin"]
    invalid["observation_cutoff_at"] = (AT - timedelta(seconds=1)).isoformat()
    with pytest.raises(ValidationError):
        Pin.model_validate(invalid)
    invalid["observation_cutoff_at"] = AT.isoformat()
    invalid["method"] = "estimated"
    invalid["source_refs"][0]["at"] = (AT + timedelta(seconds=1)).isoformat()
    with pytest.raises(ValidationError):
        Pin.model_validate(invalid)


def test_receipts_return_original_ack_after_intervening_update_and_normalize_dates(api):
    client, _ = api
    request = body()
    original = client.put(PATH, json=request).json()
    correction = copy.deepcopy(request)
    correction.pop("pin")
    correction.update(expected_revision=1, mutation_id=str(uuid.uuid4()))
    correction["content"]["behavior_code"] = "barking"
    assert client.put(PATH, json=correction).json()["revision"] == 2
    request["content"]["recorded_at"] = "2026-09-09T12:00:00+09:00"
    request["content"]["note"] = None
    assert client.put(PATH, json=request).json() == original
    assert client.get(PATH.rsplit("/", 1)[0]).json()["entries"][0]["revision"] == 2
    request["expected_revision"] = 2
    assert client.put(PATH, json=request).status_code == 409


def test_pin_and_content_cas_do_not_overwrite_each_other(api):
    client, db = api
    db.points = [raw_point()]
    request = body()
    assert client.put(PATH, json=request).status_code == 200
    final = completion(request, located_pin=True)
    corrected = {
        "expected_revision": 1,
        "mutation_id": str(uuid.uuid4()),
        "content": {**request["content"], "behavior_code": "barking"},
    }
    assert client.put(PATH, json=corrected).status_code == 200
    assert client.put(PATH + "/pin", json=final).status_code == 409
    final.update(expected_revision=2, mutation_id=str(uuid.uuid4()))
    result = client.put(PATH + "/pin", json=final)
    assert result.status_code == 200, result.text
    assert result.json()["content"]["behavior_code"] == "barking"
    assert client.put(PATH + "/pin", json=final).json() == result.json()
    final.update(expected_revision=3, expected_pin_revision=2, mutation_id=str(uuid.uuid4()))
    assert client.put(PATH + "/pin", json=final).status_code == 409


def test_delete_before_create_and_cross_operation_mutation_reuse(api):
    client, _ = api
    request = body()
    assert client.put(PATH, json=request).status_code == 200
    assert (
        client.delete(
            PATH, params={"expected_revision": 1, "mutation_id": request["mutation_id"]}
        ).status_code
        == 409
    )
    other = PATH.replace(str(ENTRY), str(uuid.uuid4()))
    deleted = client.delete(
        other, params={"expected_revision": 0, "mutation_id": str(uuid.uuid4())}
    )
    assert deleted.status_code == 200
    assert client.put(other, json=body()).status_code == 410
    assert client.put(other + "/pin", json=completion(request)).status_code == 410
    assert (
        client.delete(
            other, params={"expected_revision": 0, "mutation_id": str(uuid.uuid4())}
        ).json()
        == deleted.json()
    )


def test_write_rollback_retains_reads_retries_and_existing_completion(api, monkeypatch):
    client, _ = api
    request = body()
    assert client.put(PATH, json=request).status_code == 200
    monkeypatch.setattr(settings, "walk_entry_v2_write_enabled", False)
    caps = client.get("/app/walks/entry-capabilities").json()
    assert (
        "walk-entry-v2" in caps["read_versions"] and "walk-entry-v2" not in caps["write_versions"]
    )
    assert client.put(PATH, json=request).status_code == 200
    assert client.put(PATH + "/pin", json=completion(request)).status_code == 200
    assert client.put(PATH.replace(str(ENTRY), str(uuid.uuid4())), json=body()).status_code == 409
    monkeypatch.setattr(settings, "walk_entry_v2_enabled", False)
    assert client.get(PATH.rsplit("/", 1)[0]).status_code == 404


def test_v1_list_profile_targeted_writes_and_contexts_require_upgrade(api):
    client, _ = api
    assert client.put(PATH, json=body()).status_code == 200
    for response in [
        client.get(V1.rsplit("/", 1)[0]),
        client.get(V1 + "/contexts"),
        client.post("/app/walks/record-profile/query", json={"pet_id": str(PET)}),
        client.delete(V1, params={"expected_revision": 1, "mutation_id": str(uuid.uuid4())}),
    ]:
        assert response.status_code == 426, response.text
        assert response.json()["detail"]["code"] == "walk_entry_upgrade_required"


def test_observed_source_precision_and_legacy_read_adapter(api):
    client, db = api
    raw = raw_point(lat=37.5000004)
    db.points = [raw]
    request = body()
    request["content"]["location"] = {
        "lat": float(raw.lat),
        "lng": 127.0,
        "captured_at": raw.at.isoformat(),
        "accuracy_m": 5,
    }
    request["pin"] = located(pin(state="resolved"), method="observed", raw=raw)
    request["pin"].update(
        reason="direct_fix", uncertainty_basis="provider_accuracy", uncertainty_m=5
    )
    result = client.put(PATH, json=request)
    assert result.status_code == 200, result.text
    assert result.json()["pin"]["point"]["lat"] == float(raw.lat)
    # Legacy entry remains byte-for-byte intact; no synthetic raw references are created.
    legacy_id = uuid.uuid4()
    db.rows[legacy_id] = WalkEntry(
        walk_id=WALK, id=legacy_id, revision=1, mutation_id=uuid.uuid4(), payload=request["content"]
    )
    values = client.get(PATH.rsplit("/", 1)[0]).json()["entries"]
    legacy = next(v for v in values if v["id"] == str(legacy_id))
    assert legacy["pin_revision"] == 0
    assert legacy["pin"]["policy_version"] == "legacy-v1"
    assert legacy["pin"]["source_refs"] == []
    assert legacy_id not in db.pins


@pytest.mark.parametrize(
    "change",
    [
        {"resolution_id": str(uuid.uuid4())},
        {"target_at": (AT + timedelta(seconds=1)).isoformat()},
        {"resolve_by": (AT + timedelta(seconds=10)).isoformat()},
        {"policy_version": "new"},
        {"algorithm_version": "new"},
    ],
)
def test_resolution_identity_time_and_versions_are_immutable(api, change):
    client, _ = api
    request = body()
    assert client.put(PATH, json=request).status_code == 200
    final = completion(request)
    final["pin"].update(change)
    assert client.put(PATH + "/pin", json=final).status_code == 422


@pytest.mark.parametrize(
    "change",
    [
        {"source_refs": [{"client_seq": 99, "chain_index": 0, "at": raw_point().at.isoformat()}]},
        {"point": {"lat": 38.0, "lng": 127.0}},
    ],
)
def test_forged_raw_reference_or_copied_coordinate_rejected(api, change):
    client, db = api
    db.points = [raw_point()]
    request = body()
    request["pin"] = located(request["pin"])
    request["pin"].update(change)
    assert client.put(PATH, json=request).status_code == 422


@pytest.mark.parametrize(
    "change",
    [
        {"point": {"lat": 91, "lng": 0}},
        {"uncertainty_m": 0},
        {"unexpected": True},
        {"resolve_by": "2026-09-09T02:59:59Z"},
        {"computed_at": "2026-09-09T03:00:00"},
        {"state": "resolved"},
        {"method": "observed"},
    ],
)
def test_strict_pin_contract(change):
    with pytest.raises(ValidationError):
        Pin.model_validate({**pin(), **change})


def test_missing_required_pin_field_and_chain_interpolation_rejected():
    candidate = pin()
    for field in candidate:
        bad = dict(candidate)
        del bad[field]
        with pytest.raises(ValidationError):
            Pin.model_validate(bad)
    candidate = located(pin(), method="estimated")
    candidate["source_refs"].append({"client_seq": 1, "chain_index": 1, "at": AT.isoformat()})
    with pytest.raises(ValidationError):
        Pin.model_validate(candidate)


def test_owner_boundary_before_entry_and_unauthenticated_capabilities(api):
    client, _ = api
    assert client.put(PATH.replace(str(WALK), str(uuid.uuid4())), json=body()).status_code == 404
    service.entries.get_entry.assert_not_awaited()
    app = FastAPI()
    app.include_router(walk_entry_v2.capabilities_router)
    assert TestClient(app).get("/app/walks/entry-capabilities").status_code == 401


def test_future_only_estimation_preserves_target_and_deadline(api):
    client, db = api
    request = body()
    assert client.put(PATH, json=request).status_code == 200
    raw = raw_point(seq=1, seconds=4)
    db.points = [raw]
    final = completion(request, located_pin=True)
    located(final["pin"], method="estimated", raw=raw)
    result = client.put(PATH + "/pin", json=final)
    assert result.status_code == 200, result.text
    assert result.json()["pin"]["target_at"] == result.json()["content"]["recorded_at"]
    assert result.json()["content"]["location"] is None


def test_finish_cannot_discard_existing_coordinate_or_accept_mock(api):
    client, db = api
    db.points = [raw_point()]
    request = body()
    located(request["pin"])
    assert client.put(PATH, json=request).status_code == 200
    final = completion(request)
    final["pin"].update(method="none", point=None, source_refs=[])
    assert client.put(PATH + "/pin", json=final).status_code == 422
    db.points = [raw_point(mock=True)]
    assert client.put(PATH + "/pin", json=completion(request, located_pin=True)).status_code == 422


def test_note_creation_and_content_correction_keep_pin_separate(api):
    client, _ = api
    request = body()
    request.update(
        content={"kind": "note", "note": "  오늘의 기억  ", "recorded_at": AT.isoformat()}, pin=None
    )
    result = client.put(PATH, json=request)
    assert result.status_code == 200, result.text
    assert result.json()["content"]["note"] == "오늘의 기억"
    assert result.json()["pin"] is None
    request.update(expected_revision=1, mutation_id=str(uuid.uuid4()))
    assert (
        client.put(PATH, json=request).status_code == 422
    )  # pin=null is still a forbidden update.
    request.pop("pin")
    request["content"]["note"] = "정정"
    assert client.put(PATH, json=request).status_code == 200


def test_v1_behavior_put_does_not_mutate_v2_record_and_unknown_pet_rejected(api):
    client, _ = api
    request = body()
    bad = copy.deepcopy(request)
    bad["content"]["pet_id"] = str(uuid.uuid4())
    assert client.put(PATH, json=bad).status_code == 422
    assert client.put(PATH, json=request).status_code == 200
    v1 = copy.deepcopy(request)
    v1.pop("pin")
    v1["content"]["location"] = {"lat": 37.5, "lng": 127, "captured_at": AT.isoformat()}
    assert client.put(V1, json=v1).status_code == 426


def test_pin_revision_and_observation_window_are_checked(api):
    client, db = api
    request = body()
    assert client.put(PATH, json=request).status_code == 200
    final = completion(request)
    final["expected_pin_revision"] = 0
    assert client.put(PATH + "/pin", json=final).status_code == 409
    raw = raw_point(seq=1, seconds=9)
    db.points = [raw]
    final = completion(request, located_pin=True)
    located(final["pin"], method="estimated", raw=raw)
    final["pin"]["computed_at"] = (AT + timedelta(seconds=10)).isoformat()
    assert client.put(PATH + "/pin", json=final).status_code == 422


@pytest.mark.parametrize("eligible,expected", [(False, 200), (True, 422), (None, 422)])
def test_cached_only_pin_uses_the_same_candidate_scope_as_app(api, eligible, expected):
    client, db = api
    db.points = [raw_point().model_copy(update={"recording_eligible": eligible})]
    request = body()
    request["pin"] = completion(request)["pin"]
    response = client.put(PATH, json=request)
    assert response.status_code == expected, response.text


def test_cached_fix_cannot_certify_a_located_pin_and_mixed_valid_fix_still_matters(api):
    client, db = api
    db.points = [raw_point().model_copy(update={"recording_eligible": False})]
    request = body()
    located(request["pin"])
    assert client.put(PATH, json=request).status_code == 422
    db.points.append(raw_point(1, seconds=-1).model_copy(update={"recording_eligible": True}))
    request = body()
    request["pin"] = completion(request)["pin"]
    assert client.put(PATH, json=request).status_code == 422


def test_verified_evidence_fingerprint_is_bound_to_the_frozen_pin_request(api):
    from daengs_backend.services.walk_session.recording import recording_receipt

    client, _ = api
    request = body()
    request["recording_evidence_fingerprint"] = "sha256:" + "0" * 64
    assert client.put(PATH, json=request).status_code == 422
    request["recording_evidence_fingerprint"] = recording_receipt([]).evidence_fingerprint
    first = client.put(PATH, json=request)
    assert first.status_code == 200, first.text
    assert client.put(PATH, json=request).json() == first.json()
