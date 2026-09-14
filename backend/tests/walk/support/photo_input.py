"""Shared photo input test builders; no test cases."""

import uuid
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from daengs_backend.schemas.walk_photo import PhotoManifestWrite
from daengs_backend.services import walk_photo as service
from daengs_walk.diary.contracts.input import digest

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


def context_envelope():
    original = entry()
    payload = {"items": [], "radius_m": 250}
    return {
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


def pin_context():
    pin = {
        "resolution_id": str(uuid.uuid4()),
        "state": "resolved",
        "method": "estimated",
        "target_at": AT.isoformat(),
        "point": {"lat": 37.6, "lng": 127.1},
        "computed_at": (AT + timedelta(seconds=5)).isoformat(),
        "resolve_by": (AT + timedelta(seconds=30)).isoformat(),
        "policy_version": "walk-action-pin-v1",
        "algorithm_version": "synthetic-v1",
        "source_refs": [
            {"client_seq": 1, "chain_index": 0, "at": (AT - timedelta(seconds=5)).isoformat()},
            {"client_seq": 2, "chain_index": 0, "at": (AT + timedelta(seconds=5)).isoformat()},
        ],
        "uncertainty_m": 20.0,
        "uncertainty_basis": "model_bound",
        "reason": "refined",
    }
    sidecar = SimpleNamespace(entry_id=ENTRY, pin_revision=3, payload=pin)
    envelope = context_envelope()
    envelope["schema_version"] = "walk-entry-context-v2"
    envelope["target"].update(pin=deepcopy(pin), pin_revision=3)
    envelope["provenance"].update(
        policy_version="walk-entry-context-v2", location_basis="estimated"
    )
    return sidecar, envelope
