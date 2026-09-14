"""Candidate transport through the same validated backup boundary as motion-v1."""

import hashlib
import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from daengs_backend.core.deps import AppPrincipal, CurrentAppUser
from daengs_backend.repositories import walk_precision
from daengs_backend.schemas.walk_precision import PrecisionPoint
from daengs_backend.schemas.walk_trajectory import MAX_POINTS, VERSION, TrajectoryCalculation
from daengs_backend.services import walk_trajectory as service
from daengs_backend.services.walk_session.precision_contract import chunk_digest as precision_digest
from daengs_walk.trajectory_projection import boundaries, path_sections
from tests.walk.api import test_motion_calculation as motion_tests

boundary = motion_tests.boundary

CASES = json.loads((Path(__file__).parents[1] / "fixtures/gps-motion-replay-v1.json").read_text())[
    "cases"
]


def url(b):
    return b.path.replace("motion-calculation", "trajectory-calculation") + f"?version={VERSION}"


@pytest.mark.parametrize("boundary", [c["name"] for c in CASES], indirect=True)
def test_http_keeps_golden_measurement_times_projection_and_identity_together(boundary):
    b = boundary
    response = b.client.get(url(b))
    assert response.status_code == 200, response.text
    dto = TrajectoryCalculation.model_validate_json(response.content)
    ledger = dto.measurement.ledger
    assert dto.status == "candidate" and not dto.device_result_verified
    assert dto.observation_policy_status == "experimental"
    assert dto.measurement.scope.owner_id == str(b.owner)
    assert dto.walk_id == b.walk.id
    assert dto.measurement.scope.session_id == str(b.walk.client_session_id)
    assert not ledger.superseded
    assert dto.result_digest == dto.measurement.ref().result_digest
    assert dto.metrics == ledger.metrics()
    assert dto.metrics.walking_distance_m == pytest.approx(
        b.expected["distance_m"], rel=1e-10, abs=1e-7
    )
    assert dto.motion_recording_duration_ns == b.expected["recording_duration_nanos"]
    assert dto.boundaries == boundaries(ledger)
    assert dto.observed_runs == path_sections(ledger, walking_only=False)
    assert dto.walking_sections == path_sections(ledger, walking_only=True)
    assert dto.average_walking_speed_mps == ledger.metrics().average_walking_speed_mps
    assert [t.ref for t in dto.wall_times] == [e.ref for e in ledger.journal.events]
    assert (
        dto.wall_times[0].original_wall_time_millis
        == b.source["manifest"]["epochs"][0]["started_at_millis"]
    )
    assert (
        dto.wall_times[-1].original_wall_time_millis
        == b.source["manifest"]["epochs"][-1]["ended_at_millis"]
    )
    for location in dto.locations:
        original = b.source["raw_points"][location.ref.ingress_seq]
        # This endpoint restores the existing six-decimal raw backup before replay.
        assert location.lat == round(float(original["lat"]), 6)
        assert location.lng == round(float(original["lng"]), 6)
    times = {t.ref: t.original_wall_time_millis for t in dto.wall_times}
    for event in ledger.journal.events:
        if event.ref.ingress_seq is not None:
            original = b.source["raw_points"][event.ref.ingress_seq]
            timestamp = datetime.fromisoformat(original["at"])
            assert times[event.ref] == round(timestamp.timestamp() * 1000)
            # Invalid original time is retained independently of timeline support.
            source_ns = b.source["points"][event.ref.ingress_seq]["elapsed_realtime_nanos"]
            assert event.original_elapsed_ns == source_ns
    assert response.headers["etag"] == '"' + hashlib.sha256(response.content).hexdigest() + '"'
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["vary"] == "Authorization"
    b.session.commit.assert_not_awaited()


def test_expectation_is_checked_and_repeat_response_has_identical_bytes(boundary):
    b = boundary
    first = b.client.get(url(b))
    measurement_id = first.json()["measurement"]["measurement_id"]
    second = b.client.get(
        url(b), params={"version": VERSION, "expected_measurement_id": measurement_id}
    )
    assert second.status_code == 200, second.text
    assert second.content == first.content
    assert second.headers["etag"] == first.headers["etag"]
    changed = b.client.get(
        url(b), params={"version": VERSION, "expected_measurement_id": "shadow-" + "0" * 64}
    )
    assert changed.status_code == 409
    assert changed.json() == {"detail": {"code": "trajectory_measurement_changed"}}
    assert "etag" not in changed.headers
    assert changed.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize(
    "params", [{}, {"version": "future"}, {"version": VERSION, "expected_measurement_id": "bad"}]
)
def test_invalid_version_or_expectation_does_not_read_evidence(boundary, params):
    b = boundary
    response = b.client.get(url(b).split("?")[0], params=params)
    assert response.status_code == 422
    b.available.assert_not_awaited()
    b.backup.assert_not_awaited()


def test_owner_is_checked_even_when_the_measurement_id_is_known(boundary, monkeypatch):
    b = boundary
    first = b.client.get(url(b)).json()
    b.backup.reset_mock()
    b.client.app.dependency_overrides[CurrentAppUser.__metadata__[0].dependency] = lambda: (
        AppPrincipal(app_user_id=uuid.uuid4())
    )
    calculate = AsyncMock(side_effect=AssertionError("unauthorized CPU work"))
    monkeypatch.setattr(service.asyncio, "to_thread", calculate)
    response = b.client.get(
        url(b),
        params={
            "version": VERSION,
            "expected_measurement_id": first["measurement"]["measurement_id"],
        },
    )
    assert response.status_code == 404
    b.backup.assert_not_awaited()
    calculate.assert_not_awaited()


def test_calculation_and_serialization_run_off_event_loop_after_rollback(boundary, monkeypatch):
    b = boundary
    original = service._project
    get_input = service.walk_motion.completed_input
    event_loop_threads = []

    async def checked_input(*args):
        event_loop_threads.append(threading.get_ident())
        return await get_input(*args)

    def checked_projection(*args, **kwargs):
        assert b.session.rollback.await_count == 1
        assert threading.get_ident() != event_loop_threads[0]
        result = original(*args, **kwargs)
        assert isinstance(result, bytes)
        return result

    monkeypatch.setattr(service.walk_motion, "completed_input", checked_input)
    monkeypatch.setattr(service, "_project", checked_projection)
    assert b.client.get(url(b)).status_code == 200
    b.session.commit.assert_not_awaited()


@pytest.mark.parametrize("available", [True, False])
def test_capability_is_explicit_and_releases_read_transaction(boundary, available, monkeypatch):
    b = boundary
    b.available.return_value = available
    from daengs_backend.repositories import walk_measurement

    monkeypatch.setattr(walk_measurement, "available", AsyncMock(return_value=False))
    response = b.client.get("/app/walks/trajectory-capabilities")
    assert response.status_code == 200
    data = response.json()
    assert data["calculation_versions"] == ([VERSION] if available else [])
    assert data["max_points"] == MAX_POINTS
    assert data["delivery"] == "whole_result"
    assert not data["active_read_view_supported"]
    assert not data["persisted_measurements_supported"]
    b.session.rollback.assert_awaited_once()
    b.session.commit.assert_not_awaited()


def test_late_precision_cannot_satisfy_an_older_measurement_expectation(boundary, monkeypatch):
    b = boundary
    coarse = b.client.get(url(b)).json()["measurement"]
    precise = next(
        c
        for c in json.loads(
            (Path(__file__).parents[1] / "fixtures/gps-motion-precision-v1.json").read_text()
        )["cases"]
        if c["name"] == "walking"
    )
    row = SimpleNamespace(
        walk_id=b.walk.id,
        manifest=precise["precision_manifest"],
        manifest_fingerprint=precise["precision_manifest_fingerprint"],
        evidence_fingerprint=precise["precision_fingerprint"],
    )
    chunk = SimpleNamespace(
        chunk_index=0,
        payload=precise["precision_points"],
        fingerprint=precision_digest(
            [PrecisionPoint.model_validate(p) for p in precise["precision_points"]]
        ),
    )
    monkeypatch.setattr(walk_precision, "available", AsyncMock(return_value=True))
    monkeypatch.setattr(walk_precision, "backup", AsyncMock(return_value=row))
    monkeypatch.setattr(walk_precision, "chunks", AsyncMock(return_value=[chunk]))
    changed = b.client.get(
        url(b), params={"version": VERSION, "expected_measurement_id": coarse["measurement_id"]}
    )
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "trajectory_measurement_changed"
    latest = b.client.get(url(b)).json()["measurement"]
    assert latest["measurement_id"] != coarse["measurement_id"]
    assert latest["key"]["coordinate_basis"] == "device-fix-bits-v1"
    assert latest["key"]["precision_fingerprint"] == precise["precision_fingerprint"][7:]


def test_point_limit_rejects_without_cpu_or_truncation(boundary, monkeypatch):
    b = boundary
    monkeypatch.setattr(service, "MAX_POINTS", len(b.source["points"]) - 1)
    projection = AsyncMock(side_effect=AssertionError("oversize replay"))
    monkeypatch.setattr(service.asyncio, "to_thread", projection)
    response = b.client.get(url(b))
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "trajectory_point_limit"
    projection.assert_not_awaited()
    b.session.rollback.assert_awaited_once()


def test_openapi_publishes_opt_in_version_and_candidate_response(boundary):
    schema = boundary.client.app.openapi()
    operation = schema["paths"]["/app/walks/{walk_id}/trajectory-calculation"]["get"]
    version = next(p for p in operation["parameters"] if p["name"] == "version")
    assert version["required"] and version["schema"]["const"] == VERSION
    response = operation["responses"]["200"]
    assert "ETag" in response["headers"]
    assert response["content"]["application/json"]["schema"]["$ref"].endswith(
        "/TrajectoryCalculation"
    )
    properties = schema["components"]["schemas"]["TrajectoryCalculation"]["properties"]
    assert properties["status"]["const"] == "candidate"
    assert properties["device_result_verified"]["const"] is False
    assert properties["observation_policy_status"]["const"] == "experimental"
