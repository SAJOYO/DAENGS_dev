"""Actual HTTP/schema/chunk/repair boundaries; no shared database or broker."""

import copy
import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import AppPrincipal, CurrentAppUser
from daengs_backend.routers import walk as router
from daengs_backend.schemas.walk import WalkFinalizeRequest, WalkPointUpload, WalkUpload
from daengs_backend.schemas.walk_entry_v2 import EntryWriteV2
from daengs_backend.services import walk_recording as recording
from daengs_backend.services.walk_chunk import decode_chunk, encode_chunk
from daengs_backend.services.walk_entry_pin import validate_new_pin, validate_sources
from daengs_backend.services.walk_finalize import prepare_finalized_walk

FIXTURE = Path(__file__).parents[1] / "fixtures/gps-recording-v1.json"


def contract():
    # Optional artifact comes from the Android production serializer test in this paired change.
    return json.loads(
        Path(os.getenv("GPS_APP_CONTRACT_FILE", str(FIXTURE))).read_text(encoding="utf-8")
    )


@pytest.fixture
def wire(monkeypatch):
    data = contract()
    upload = WalkUpload.model_validate(data["upload"])
    points = [p.model_copy(update={"recording_eligible": None}) for p in upload.points]
    chunk = SimpleNamespace(
        seq_from=0, seq_to=len(points) - 1, point_count=len(points), payload=encode_chunk(points)
    )
    owner, walk_id = uuid.uuid4(), uuid.uuid4()
    walk = SimpleNamespace(
        id=walk_id,
        client_session_id=upload.client_session_id,
        pet_ids=[],
        started_at=upload.started_at,
        ended_at=upload.ended_at,
        weather_code=None,
        is_day=None,
        temperature_c=None,
        points=[chunk],
        analysis_state="derived",
    )
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    monkeypatch.setattr(
        recording.walks,
        "get_owned_for_update",
        AsyncMock(side_effect=lambda s, o, w: walk if o == owner and w == walk_id else None),
    )
    monkeypatch.setattr(
        recording.walks,
        "get_owned",
        AsyncMock(side_effect=lambda s, o, w: walk if o == owner and w == walk_id else None),
    )
    monkeypatch.setattr(recording.entries, "contains_v2", AsyncMock(return_value=False))
    monkeypatch.setattr(recording.entries, "raw_chunks", AsyncMock(return_value=[chunk]))
    app = FastAPI()
    app.include_router(router.router)
    app.dependency_overrides[CurrentAppUser.__metadata__[0].dependency] = lambda: AppPrincipal(
        app_user_id=owner
    )
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app), walk, session, data


def repair_body(data):
    return {
        "contract_version": "gps-recording-v1",
        "policy_version": "gps-recording-eligibility-v1",
        "raw_input_fingerprint": data["expected_receipt"]["raw_input_fingerprint"],
        "points": data["upload"]["points"],
    }


def test_repair_roundtrip_and_retry_preserve_original_analysis_identity(wire):
    client, walk, session, data = wire
    manifest = WalkFinalizeRequest(expected_point_count=1, terminal_client_seq=0)
    before = prepare_finalized_walk(walk.points, manifest).input_fingerprint
    path = f"/app/walks/{walk.id}/recording-evidence"
    for _ in range(2):
        response = client.put(path, json=repair_body(data))
        assert response.status_code == 200, response.text
        assert response.json() == data["expected_receipt"]
    detail = client.get(f"/app/walks/{walk.id}").json()
    assert detail["points"][0]["recording_eligible"] is False
    assert detail["recording_receipt"] == data["expected_receipt"]
    assert prepare_finalized_walk(walk.points, manifest).input_fingerprint == before
    assert session.rollback.await_count == 0


@pytest.mark.parametrize(
    "change,code",
    [
        ("raw", "recording_raw_mismatch"),
        ("hash", "recording_raw_mismatch"),
        ("flag", "recording_metadata_conflict"),
    ],
)
def test_conflicting_repair_preserves_payload(wire, change, code):
    client, walk, _, data = wire
    request = repair_body(copy.deepcopy(data))
    path = f"/app/walks/{walk.id}/recording-evidence"
    if change == "flag":
        assert client.put(path, json=request).status_code == 200
        request["points"][0]["recording_eligible"] = True
    elif change == "raw":
        request["points"][0]["lat"] += 1
    else:
        request["raw_input_fingerprint"] = "sha256:" + "0" * 64
    before = copy.deepcopy(walk.points[0].payload)
    response = client.put(path, json=request)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == code
    assert walk.points[0].payload == before


def test_owner_and_accepted_pin_protect_metadata(wire, monkeypatch):
    client, walk, _, data = wire
    request = repair_body(data)
    assert (
        client.put(f"/app/walks/{uuid.uuid4()}/recording-evidence", json=request).status_code == 404
    )
    monkeypatch.setattr(recording.entries, "contains_v2", AsyncMock(return_value=True))
    response = client.put(f"/app/walks/{walk.id}/recording-evidence", json=request)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "recording_metadata_locked"
    assert decode_chunk(walk.points[0].payload)[0].recording_eligible is None


@pytest.mark.asyncio
async def test_app_wire_codec_and_actual_pin_validator_agree(wire):
    _, walk, session, data = wire
    upload = WalkUpload.model_validate(data["upload"])
    walk.points[0].payload = encode_chunk(upload.points)
    stored = decode_chunk(walk.points[0].payload)
    assert recording.recording_receipt(stored).model_dump() == data["expected_receipt"]
    body = EntryWriteV2.model_validate(data["pin_request"])
    validate_new_pin(body.content, body.pin)
    await validate_sources(session, walk.id, body.content, body.pin)


@pytest.mark.parametrize("value", ["false", 0, 1])
def test_invalid_eligibility_is_not_silently_coerced(value):
    point = contract()["upload"]["points"][0]
    point["recording_eligible"] = value
    with pytest.raises(ValidationError):
        WalkPointUpload.model_validate(point)


def test_old_chunks_and_unknown_metadata_keep_legacy_meaning():
    point = WalkUpload.model_validate(contract()["upload"]).points[0]
    legacy = point.model_copy(update={"recording_eligible": None})
    payload = encode_chunk([legacy])
    assert payload["v"] == 1
    assert decode_chunk(payload)[0].recording_eligible is None
    enriched = encode_chunk([point])
    enriched["recording_policy"] = "future-policy"
    with pytest.raises(ValueError):
        decode_chunk(enriched)
