"""Frozen pre-extraction wire bytes and query/storage dependency boundaries."""

import ast
import hashlib
import json
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from daengs_backend.schemas.walk import WalkPointUpload
from daengs_backend.schemas.walk_measurement import MeasurementSummary, RoutePage
from daengs_backend.schemas.walk_motion import MotionManifest, MotionObservation
from daengs_backend.schemas.walk_precision import PrecisionPoint
from daengs_backend.schemas.walk_trajectory import TrajectoryCalculation
from daengs_backend.services import walk_measurement as storage
from daengs_backend.services import walk_measurement_projection as projection
from daengs_backend.services import walk_trajectory as query
from daengs_backend.services.walk_motion_contract import MotionConflict
from daengs_backend.services.walk_precision_contract import refine_points
from tests.walk.measurement.test_stored_measurement import FIXTURES, OWNER, WALK

FROZEN = json.loads((FIXTURES / "measurement-projection-v1.json").read_text())


def sha(value):
    return hashlib.sha256(value).hexdigest()


def inputs(record):
    cases = json.loads((FIXTURES / record["fixture"]).read_text())["cases"]
    case = next(c for c in cases if c["name"] == record["name"])
    raw = [WalkPointUpload.model_validate(p) for p in case["raw_points"]]
    if "precision_points" in case:
        raw = refine_points(
            [PrecisionPoint.model_validate(p) for p in case["precision_points"]], raw
        )
    return (
        MotionManifest.model_validate(case["manifest"]),
        raw,
        [MotionObservation.model_validate(p) for p in case["points"]],
        case["evidence_fingerprint"],
        case.get("precision_fingerprint"),
    )


@pytest.mark.parametrize("record", FROZEN["cases"], ids=lambda r: r["fixture"] + ":" + r["name"])
def test_projection_preserves_pre_extraction_bytes_ids_and_page_hashes(record):
    evidence = inputs(record)
    result = projection.project(*evidence, owner=OWNER)
    assert result.measurement.measurement_id == record["measurement_id"]
    response = query._project(*evidence, owner=OWNER, walk_id=WALK, expected=None)
    assert sha(response) == record["response_sha256"]
    if record["summary_sha256"] is not None:
        summary, pages = storage.project(evidence, OWNER, WALK)
        assert sha(summary.model_dump_json().encode()) == record["summary_sha256"]
        assert [sha(p.encode()) for p in pages] == record["page_sha256"]


@pytest.mark.parametrize("contract", [TrajectoryCalculation, MeasurementSummary, RoutePage])
def test_wire_schemas_are_unchanged(contract):
    assert (
        sha(json.dumps(contract.model_json_schema(), sort_keys=True).encode())
        == FROZEN["schemas"][contract.__name__]
    )


def test_storage_does_not_build_or_parse_a_query_response(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("storage must not use query serialization")

    monkeypatch.setattr(query, "_project", blocked)
    monkeypatch.setattr(TrajectoryCalculation, "model_validate_json", blocked)
    monkeypatch.setattr(TrajectoryCalculation, "model_dump_json", blocked)
    record = next(r for r in FROZEN["cases"] if r["summary_sha256"])
    summary, pages = storage.project(inputs(record), OWNER, WALK)
    assert sha(summary.model_dump_json().encode()) == record["summary_sha256"]
    assert [sha(p.encode()) for p in pages] == record["page_sha256"]


def test_projection_and_storage_have_no_query_service_or_schema_imports():
    for module in (projection, storage):
        tree = ast.parse(Path(module.__file__).read_text())
        imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        assert "daengs_backend.schemas.walk_trajectory" not in imports
        assert "daengs_backend.services.walk_trajectory" not in imports
    # A fresh interpreter also catches indirect imports, not just spelling in two files.
    program = """
import sys
import tests.conftest
class BlockQuery:
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {"daengs_backend.services.walk_trajectory", "daengs_backend.schemas.walk_trajectory"}:
            raise AssertionError(fullname)
sys.meta_path.insert(0, BlockQuery())
from daengs_backend.services import walk_measurement, walk_measurement_projection
"""
    subprocess.run([sys.executable, "-c", program], check=True, capture_output=True, text=True)


@pytest.fixture
def prepared(monkeypatch):
    record = next(r for r in FROZEN["cases"] if r["summary_sha256"])
    evidence = inputs(record)
    session = SimpleNamespace(
        rollback=AsyncMock(), commit=AsyncMock(), flush=AsyncMock(), add=Mock(), add_all=Mock()
    )
    monkeypatch.setattr(storage, "owned", AsyncMock())
    monkeypatch.setattr(storage.walk_motion, "completed_input", AsyncMock(return_value=evidence))
    monkeypatch.setattr(storage.repo, "by_input", AsyncMock(return_value=None))
    monkeypatch.setattr(
        storage.motion_repo,
        "backup",
        AsyncMock(return_value=SimpleNamespace(evidence_fingerprint=evidence[3])),
    )
    monkeypatch.setattr(
        storage.precision_repo,
        "backup",
        AsyncMock(return_value=SimpleNamespace(evidence_fingerprint=evidence[4])),
    )
    return session, evidence, record


async def test_prepare_projects_off_loop_then_publishes_exact_summary_and_pages(
    prepared, monkeypatch
):
    session, evidence, record = prepared
    loop_thread = threading.get_ident()
    original = storage.project

    def checked(*args):
        assert threading.get_ident() != loop_thread
        assert session.rollback.await_count == 2
        session.add.assert_not_called()
        return original(*args)

    monkeypatch.setattr(storage, "project", checked)
    response = await storage.prepare(session, OWNER, WALK)
    assert sha(response) == record["summary_sha256"]
    row = session.add.call_args.args[0]
    assert row.measurement_id == record["measurement_id"]
    assert row.fingerprint == sha(response)
    assert row.input_key == storage.input_key(evidence[3], evidence[4])
    pages = session.add_all.call_args.args[0]
    assert [p.fingerprint for p in pages] == record["page_sha256"]
    assert all(p.fingerprint == sha(p.payload.encode()) for p in pages)
    session.commit.assert_awaited_once()


async def test_prepare_rechecks_evidence_before_writing(prepared, monkeypatch):
    session, _, _ = prepared
    monkeypatch.setattr(
        storage.precision_repo,
        "backup",
        AsyncMock(return_value=SimpleNamespace(evidence_fingerprint="changed")),
    )
    with pytest.raises(MotionConflict) as failure:
        await storage.prepare(session, OWNER, WALK)
    assert failure.value.code == "measurement_input_changed"
    session.add.assert_not_called()
    session.commit.assert_not_awaited()
    assert session.rollback.await_count == 3


async def test_existing_measurement_bypasses_projection(prepared, monkeypatch):
    session, evidence, record = prepared
    summary, _ = storage.project(evidence, OWNER, WALK)
    payload = summary.model_dump_json()
    monkeypatch.setattr(
        storage.repo,
        "by_input",
        AsyncMock(
            return_value=SimpleNamespace(payload=payload, fingerprint=record["summary_sha256"])
        ),
    )
    monkeypatch.setattr(storage, "project", Mock(side_effect=AssertionError("already stored")))
    assert await storage.prepare(session, OWNER, WALK) == payload.encode()
    session.commit.assert_not_awaited()


async def test_projection_failure_cannot_publish_partial_output(prepared, monkeypatch):
    session, _, _ = prepared
    monkeypatch.setattr(storage, "project", Mock(side_effect=ValueError("invalid")))
    with pytest.raises(MotionConflict) as failure:
        await storage.prepare(session, OWNER, WALK)
    assert failure.value.code == "measurement_calculation_invalid_input"
    session.add.assert_not_called()
    session.add_all.assert_not_called()
    session.commit.assert_not_awaited()
