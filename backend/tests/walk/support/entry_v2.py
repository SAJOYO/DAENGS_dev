"""Shared entry v2 test builders; no test cases."""

import copy
import uuid
from datetime import UTC, datetime, timedelta

from daengs_backend.schemas.walk import WalkPointUpload

OWNER, PET, WALK, ENTRY = [uuid.uuid4() for _ in range(4)]

AT = datetime(2026, 9, 9, 3, tzinfo=UTC)


def raw_point(seq=0, *, seconds=-2, lat=37.5, mock=False):
    return WalkPointUpload(
        client_seq=seq,
        chain_index=0,
        at=AT + timedelta(seconds=seconds),
        lat=lat,
        lng=127,
        accuracy_m=5,
        is_mock=mock,
    )


def pin(*, state="provisional", method="none"):
    return {
        "resolution_id": str(uuid.uuid4()),
        "state": state,
        "method": method,
        "target_at": AT.isoformat(),
        "point": None,
        "computed_at": AT.isoformat(),
        "resolve_by": (AT + timedelta(seconds=8)).isoformat(),
        "policy_version": "action-pin-policy-v1",
        "algorithm_version": "action-pin-local-v1",
        "source_refs": [],
        "uncertainty_m": None,
        "uncertainty_basis": "unknown",
        "reason": "awaiting_observations" if state == "provisional" else "deadline",
    }


def body():
    return {
        "expected_revision": 0,
        "mutation_id": str(uuid.uuid4()),
        "content": {
            "kind": "behavior",
            "behavior_code": "sniffing",
            "pet_id": str(PET),
            "recorded_at": AT.isoformat(),
        },
        "pin": pin(),
    }


def located(value, *, method="last_known", raw=None):
    raw = raw or raw_point()
    value.update(
        method=method,
        point={"lat": float(raw.lat), "lng": float(raw.lng)},
        source_refs=[
            {"client_seq": raw.client_seq, "chain_index": raw.chain_index, "at": raw.at.isoformat()}
        ],
    )
    return value


def completion(created, *, located_pin=False):
    result = copy.deepcopy(created["pin"])
    result.update(
        state="resolved" if located_pin else "unlocated",
        reason="deadline",
        computed_at=(AT + timedelta(seconds=8)).isoformat(),
    )
    if located_pin:
        located(result)
    return {
        "expected_revision": 1,
        "expected_pin_revision": 1,
        "mutation_id": str(uuid.uuid4()),
        "pin": result,
    }
