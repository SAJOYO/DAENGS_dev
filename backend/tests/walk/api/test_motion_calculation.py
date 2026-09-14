"""HTTP owner/completion boundary; real SQL round trips live in the explicit disposable tool."""

import copy
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import AppPrincipal, CurrentAppUser
from daengs_backend.repositories import walk_motion as repo
from daengs_backend.routers.walk_motion import router
from daengs_backend.schemas.walk import WalkPointUpload
from daengs_backend.schemas.walk_motion import CHUNK_SIZE, MotionObservation
from daengs_backend.services.walk_session.chunk import encode_chunk
from daengs_backend.services.walk_session.motion_contract import chunk_digest


@pytest.fixture
def boundary(monkeypatch, request):
    from daengs_backend.repositories import walk_precision

    monkeypatch.setattr(walk_precision, "available", AsyncMock(return_value=False))
    case = next(
        c
        for c in json.loads(
            (Path(__file__).parents[1] / "fixtures/gps-motion-replay-v1.json").read_text()
        )["cases"]
        if c["name"] == getattr(request, "param", "walking")
    )
    case = copy.deepcopy(case)
    owner, walk_id = uuid.uuid4(), uuid.uuid4()
    row = SimpleNamespace(
        manifest=case["manifest"],
        manifest_fingerprint=case["manifest_fingerprint"],
        evidence_fingerprint=case["evidence_fingerprint"],
    )
    walk = SimpleNamespace(
        id=walk_id,
        client_session_id=uuid.UUID(case["manifest"]["client_session_id"]),
        analysis_state="derived",
        ended_at=datetime.fromtimestamp(
            case["manifest"]["epochs"][-1]["ended_at_millis"] / 1000, UTC
        ),
    )
    chunks = [
        SimpleNamespace(
            chunk_index=start // CHUNK_SIZE,
            payload=case["points"][start : start + CHUNK_SIZE],
            fingerprint=chunk_digest(
                [
                    MotionObservation.model_validate(p)
                    for p in case["points"][start : start + CHUNK_SIZE]
                ]
            ),
        )
        for start in range(0, len(case["points"]), CHUNK_SIZE)
    ]
    raw = [
        SimpleNamespace(
            payload=encode_chunk(
                [
                    WalkPointUpload.model_validate(p)
                    for p in case["raw_points"][start : start + CHUNK_SIZE]
                ]
            )
        )
        for start in range(0, len(case["raw_points"]), CHUNK_SIZE)
    ]
    session = SimpleNamespace(
        rollback=AsyncMock(),
        commit=AsyncMock(side_effect=AssertionError("read endpoint committed")),
    )
    available, backup = AsyncMock(return_value=True), AsyncMock(return_value=row)
    monkeypatch.setattr(repo, "available", available)
    monkeypatch.setattr(repo, "backup", backup)
    monkeypatch.setattr(
        repo,
        "owned_locked",
        AsyncMock(
            side_effect=lambda s, user, id: walk if user == owner and id == walk_id else None
        ),
    )
    monkeypatch.setattr(repo, "chunks", AsyncMock(return_value=chunks))
    monkeypatch.setattr(repo, "raw_chunks", AsyncMock(return_value=raw))
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[CurrentAppUser.__metadata__[0].dependency] = lambda: AppPrincipal(
        app_user_id=owner
    )
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            path=f"/app/walks/{walk_id}/motion-calculation",
            row=row,
            chunks=chunks,
            raw=raw,
            walk=walk,
            session=session,
            available=available,
            backup=backup,
            expected=case["expected"],
            source=case,
            owner=owner,
        )


def test_calculation_detaches_before_cpu_work_and_preserves_backup_contract(boundary, monkeypatch):
    from daengs_backend.services import walk_motion_calculation as service

    original = service.replay

    def checked(*args):
        assert boundary.session.rollback.await_count == 1
        return original(*args)

    monkeypatch.setattr(service, "replay", checked)
    response = boundary.client.get(boundary.path)
    assert response.status_code == 200, response.text
    data = response.json()
    for key, value in boundary.expected.items():
        assert data[key] == pytest.approx(value) if isinstance(value, float) else data[key] == value
    assert data["device_result_verified"] is False
    assert data["coordinate_basis"] == "stored-raw-v1-six-decimals"
    assert data["evidence_fingerprint"] == boundary.row.evidence_fingerprint
    caps = boundary.client.get("/app/walks/motion-capabilities").json()
    assert caps["calculation_verified"] is False
    assert caps["calculation_versions"] == ["gps-motion-calculation-v1"]
    boundary.session.commit.assert_not_awaited()


def test_missing_storage_does_not_advertise_calculation(boundary):
    boundary.available.return_value = False
    response = boundary.client.get("/app/walks/motion-capabilities")
    assert response.status_code == 200
    assert response.json()["calculation_versions"] == []
    assert response.json()["backup_supported"] is False


@pytest.mark.parametrize(
    "damage", [None, "collecting", "hash", "missing_chunk", "base", "client", "point"]
)
@pytest.mark.parametrize("calculation", ["motion", "trajectory"])
def test_precision_calculation_uses_sealed_bound_bits(boundary, monkeypatch, damage, calculation):
    from daengs_backend.repositories import walk_precision as precision
    from daengs_backend.schemas.walk_precision import PrecisionPoint
    from daengs_backend.services.walk_session.precision_contract import (
        chunk_digest as precision_digest,
    )

    case = next(
        c
        for c in json.loads(
            (Path(__file__).parents[1] / "fixtures/gps-motion-precision-v1.json").read_text(
                encoding="utf-8"
            )
        )["cases"]
        if c["name"] == "walking"
    )
    row = SimpleNamespace(
        walk_id=boundary.walk.id,
        manifest=case["precision_manifest"],
        manifest_fingerprint=case["precision_manifest_fingerprint"],
        evidence_fingerprint=case["precision_fingerprint"],
    )
    chunks = [
        SimpleNamespace(
            chunk_index=0,
            payload=case["precision_points"],
            fingerprint=precision_digest(
                [PrecisionPoint.model_validate(p) for p in case["precision_points"]]
            ),
        )
    ]
    monkeypatch.setattr(precision, "available", AsyncMock(return_value=True))
    monkeypatch.setattr(precision, "backup", AsyncMock(return_value=row))
    monkeypatch.setattr(precision, "chunks", AsyncMock(return_value=chunks))
    if damage == "collecting":
        row.evidence_fingerprint = None
    if damage == "hash":
        row.evidence_fingerprint = "sha256:" + "0" * 64
    if damage == "missing_chunk":
        chunks.clear()
    if damage == "base":
        row.manifest["base_evidence_fingerprint"] = "sha256:" + "0" * 64
    if damage == "client":
        row.manifest["client_session_id"] = str(uuid.uuid4())
    if damage == "point":
        chunks[0].payload[0]["lat_bits"] = "0000000000000000"
    path = boundary.path
    if calculation == "trajectory":
        path = path.replace("motion-calculation", "trajectory-calculation")
        path += "?version=walk-trajectory-calculation-v1"
    response = boundary.client.get(path)
    assert response.status_code == (409 if damage else 200), response.text
    if damage is None:
        data = response.json()
        if calculation == "trajectory":
            key = data["measurement"]["key"]
            assert key["coordinate_basis"] == "device-fix-bits-v1"
            assert key["precision_fingerprint"] == row.evidence_fingerprint[7:]
            assert data["metrics"]["walking_distance_m"] == pytest.approx(
                case["expected"]["distance_m"], rel=1e-10, abs=1e-7
            )
            assert data["device_result_verified"] is False
            return
        assert data["coordinate_basis"] == "device-fix-bits-v1"
        assert data["precision_fingerprint"] == row.evidence_fingerprint
        assert data["device_result_verified"] is False
        assert data["distance_m"] == pytest.approx(
            case["expected"]["distance_m"], rel=1e-10, abs=1e-7
        )
        assert data["segments"] == case["expected"]["segments"]


@pytest.mark.parametrize(
    "damage,status",
    [
        ("schema", 503),
        ("missing", 404),
        ("owner", 404),
        ("collecting", 409),
        ("missing_chunk", 409),
        ("reordered", 409),
        ("payload", 409),
        ("raw", 409),
        ("evidence", 409),
        ("manifest", 409),
        ("future", 409),
        ("end", 409),
        ("not_finalized", 409),
    ],
)
@pytest.mark.parametrize("calculation", ["motion", "trajectory"])
def test_unverified_inputs_never_return_a_calculated_success(boundary, damage, status, calculation):
    b = boundary
    if damage == "schema":
        b.available.return_value = False
    elif damage == "missing":
        b.backup.return_value = None
    elif damage == "owner":
        b.path = f"/app/walks/{uuid.uuid4()}/motion-calculation"
    elif damage == "collecting":
        b.row.evidence_fingerprint = None
    elif damage == "missing_chunk":
        b.chunks.clear()
    elif damage == "reordered":
        b.chunks[0].payload.reverse()
    elif damage == "payload":
        b.chunks[0].payload[0]["speed_mps_bits"] = "00000000"
    elif damage == "raw":
        b.raw[0].payload["pts"][0][3] += 1
    elif damage == "evidence":
        b.row.evidence_fingerprint = "sha256:" + "0" * 64
    elif damage == "manifest":
        b.row.manifest_fingerprint = "sha256:" + "0" * 64
    elif damage == "future":
        b.row.manifest["policy"]["measurement_version"] = "future"
    elif damage == "end":
        b.walk.ended_at = datetime(2020, 1, 1, tzinfo=UTC)
    elif damage == "not_finalized":
        b.walk.analysis_state = "raw_uploaded"
    path = b.path
    if calculation == "trajectory":
        path = path.replace("motion-calculation", "trajectory-calculation")
        path += "?version=walk-trajectory-calculation-v1"
    response = b.client.get(path)
    assert response.status_code == status, response.text
    b.session.rollback.assert_awaited_once()
    b.session.commit.assert_not_awaited()
