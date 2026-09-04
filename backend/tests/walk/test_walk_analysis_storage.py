import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from daengs_backend.models.walk import (
    Walk,
    WalkAnalysis,
    WalkCapsule,
    WalkCellophaneSheet,
)
from daengs_backend.repositories import walk as walk_repo
from daengs_backend.services.walk_analysis import (
    CELLOPHANE_CELL_COLUMNS,
    CELLOPHANE_SHEET_SCHEMA_VERSION,
    build_analysis_models,
    cellophane_sheet_fingerprint,
    decode_analysis_model,
    decode_cellophane,
    decode_stored_cellophane,
    encode_cellophane,
)
from daengs_backend.services.walk_finalize import PreparedWalkEvidence
from daengs_walk import WalkEvidencePoint, analyze_walk, build_cellophane

REPO = Path(__file__).parents[3]
WALK_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
STARTED_AT = datetime(2026, 9, 1, 9, tzinfo=UTC)
INPUT_FINGERPRINT = "sha256:" + "1" * 64


def calculation():
    points = tuple(
        WalkEvidencePoint(
            client_seq=seq,
            chain_index=0,
            at=STARTED_AT + timedelta(seconds=seq * 10),
            lat=37.5,
            lng=127 + seq * 0.000113,
            accuracy_m=8,
        )
        for seq in range(7)
    )
    prepared = PreparedWalkEvidence(
        points=points,
        input_fingerprint=INPUT_FINGERPRINT,
        point_count=len(points),
        terminal_client_seq=len(points) - 1,
    )
    evidence = analyze_walk(
        WALK_ID,
        STARTED_AT,
        STARTED_AT + timedelta(seconds=60),
        points,
    )
    return prepared, evidence, build_cellophane(evidence)


def constraint_names(table) -> set[str]:
    return {constraint.name for constraint in table.constraints if constraint.name is not None}


def test_analysis_model_pins_input_and_each_contract_version() -> None:
    prepared, evidence, sheet = calculation()

    analysis = build_analysis_models(prepared, evidence, sheet)

    assert analysis.walk_id == WALK_ID
    assert analysis.input_fingerprint == INPUT_FINGERPRINT
    assert analysis.point_count == 7
    assert analysis.terminal_client_seq == 6
    assert analysis.facts_record_version == 1
    assert analysis.calculation_version == 4
    assert analysis.receipt_version == 1
    assert analysis.observation_version == 1
    assert analysis.moving_distance_m == evidence.facts.moving_distance_m
    assert analysis.facts["walk_id"] == str(WALK_ID)
    assert analysis.measurement_receipt["received_fix_count"] == 7
    assert len(analysis.micro_observations) == len(evidence.observations)
    decoded = decode_analysis_model(analysis)
    assert decoded.facts == evidence.facts
    assert decoded.measurement_receipt == evidence.receipt
    assert decoded.motion_events == evidence.events
    assert decoded.micro_observations == evidence.observations

    (stored_sheet,) = analysis.cellophane_sheets
    assert stored_sheet.paint_fp == sheet.paint_fp
    assert stored_sheet.sheet_schema_version == CELLOPHANE_SHEET_SCHEMA_VERSION
    assert stored_sheet.cell_count == len(sheet.occupancy)
    assert stored_sheet.analysis is analysis
    assert decode_stored_cellophane(stored_sheet) == sheet


def test_read_boundary_rejects_columns_that_disagree_with_payloads() -> None:
    prepared, evidence, sheet = calculation()
    analysis = build_analysis_models(prepared, evidence, sheet)
    analysis.moving_s += 1

    with pytest.raises(ValueError, match="summary"):
        decode_analysis_model(analysis)

    analysis = build_analysis_models(prepared, evidence, sheet)
    stored_sheet = analysis.cellophane_sheets[0]
    stored_sheet.cell_count += 1
    with pytest.raises(ValueError, match="metadata"):
        decode_stored_cellophane(stored_sheet)


def test_empty_walk_still_has_a_versioned_empty_sheet() -> None:
    prepared = PreparedWalkEvidence(
        points=(),
        input_fingerprint=INPUT_FINGERPRINT,
        point_count=0,
        terminal_client_seq=None,
    )
    evidence = analyze_walk(WALK_ID, STARTED_AT, STARTED_AT, ())
    sheet = build_cellophane(evidence)

    analysis = build_analysis_models(prepared, evidence, sheet)
    stored_sheet = analysis.cellophane_sheets[0]

    assert analysis.point_count == 0
    assert analysis.terminal_client_seq is None
    assert stored_sheet.cell_count == 0
    assert stored_sheet.payload["cells"] == []
    assert decode_stored_cellophane(stored_sheet) == sheet

    analysis.observation_version = 999
    with pytest.raises(ValueError, match="version"):
        decode_analysis_model(analysis)


def test_compact_sheet_is_independent_of_dict_insertion_order() -> None:
    _prepared, _evidence, sheet = calculation()
    reversed_sheet = replace(
        sheet,
        occupancy=dict(reversed(list(sheet.occupancy.items()))),
        peak=dict(reversed(list(sheet.peak.items()))),
    )

    first = encode_cellophane(sheet)
    second = encode_cellophane(reversed_sheet)

    assert first == second
    assert first["cols"] == list(CELLOPHANE_CELL_COLUMNS)
    assert first["cells"] == sorted(first["cells"], key=lambda row: (row[0], row[1]))
    assert cellophane_sheet_fingerprint(first) == cellophane_sheet_fingerprint(second)


def test_sheet_fingerprint_rejects_changed_cell_values() -> None:
    _prepared, _evidence, sheet = calculation()
    payload = encode_cellophane(sheet)
    fingerprint = cellophane_sheet_fingerprint(payload)
    changed = json.loads(json.dumps(payload))
    changed["cells"][0][2] += 1

    with pytest.raises(ValueError, match="fingerprint"):
        decode_cellophane(changed, expected_fingerprint=fingerprint)


def test_sheet_decoder_rejects_noncanonical_order_and_count() -> None:
    _prepared, _evidence, sheet = calculation()
    payload = encode_cellophane(sheet)
    payload["cells"] = list(reversed(payload["cells"]))
    with pytest.raises(ValueError, match="q/r 순"):
        decode_cellophane(payload)

    payload = encode_cellophane(sheet)
    payload["cell_count"] += 1
    with pytest.raises(ValueError, match="cell_count"):
        decode_cellophane(payload)


def test_builder_rejects_a_receipt_from_another_input() -> None:
    prepared, evidence, sheet = calculation()
    forged = replace(prepared, point_count=prepared.point_count + 1)

    with pytest.raises(ValueError, match="점 개수"):
        build_analysis_models(forged, evidence, sheet)


def test_sqlalchemy_metadata_keeps_state_identity_and_cascade_contracts() -> None:
    assert Walk.__table__.c.analysis_state.nullable is False
    assert str(Walk.__table__.c.analysis_state.server_default.arg) == "'collecting'"
    assert Walk.analysis_state.property.deferred is True
    assert "walks_analysis_state_check" in constraint_names(Walk.__table__)
    assert "walks_weather_code_range" in constraint_names(Walk.__table__)
    assert "walks_temperature_c_range" in constraint_names(Walk.__table__)

    analysis_constraints = constraint_names(WalkAnalysis.__table__)
    assert "walk_analyses_identity_unique" in analysis_constraints
    identity = next(
        item
        for item in WalkAnalysis.__table__.constraints
        if isinstance(item, UniqueConstraint) and item.name == "walk_analyses_identity_unique"
    )
    assert [column.name for column in identity.columns] == [
        "walk_id",
        "input_fingerprint",
        "facts_record_version",
        "calculation_version",
        "receipt_version",
        "observation_version",
    ]
    walk_fk = next(iter(WalkAnalysis.__table__.c.walk_id.foreign_keys))
    assert walk_fk.ondelete == "CASCADE"

    assert [column.name for column in WalkCellophaneSheet.__table__.primary_key.columns] == [
        "analysis_id",
        "paint_fp",
    ]
    sheet_fk = next(iter(WalkCellophaneSheet.__table__.c.analysis_id.foreign_keys))
    assert sheet_fk.ondelete == "CASCADE"
    assert "walk_cellophane_payload_object" in constraint_names(WalkCellophaneSheet.__table__)

    assert [column.name for column in WalkCapsule.__table__.primary_key.columns] == [
        "analysis_id"
    ]
    capsule_fk = next(iter(WalkCapsule.__table__.c.analysis_id.foreign_keys))
    assert capsule_fk.ondelete == "CASCADE"
    assert "walk_capsules_capabilities_array" in constraint_names(WalkCapsule.__table__)
    assert "walk_capsules_context_object" in constraint_names(WalkCapsule.__table__)


def test_walk_select_stays_compatible_until_manual_migration_runs() -> None:
    compiled = str(select(Walk).compile(dialect=postgresql.dialect()))

    assert "walks.analysis_state" not in compiled


async def test_finalize_query_loads_state_and_locks_the_walk_row() -> None:
    session = AsyncMock()
    session.scalar.return_value = None

    await walk_repo.get_owned_for_update(session, uuid.uuid4(), uuid.uuid4())

    stmt = session.scalar.await_args.args[0]
    compiled = str(stmt.compile(dialect=postgresql.dialect()))
    assert "walks.analysis_state" in compiled
    assert "FOR UPDATE" in compiled


def test_models_compile_to_postgresql_jsonb_contract() -> None:
    analysis_sql = str(
        CreateTable(WalkAnalysis.__table__).compile(dialect=postgresql.dialect())
    )
    sheet_sql = str(
        CreateTable(WalkCellophaneSheet.__table__).compile(dialect=postgresql.dialect())
    )
    capsule_sql = str(
        CreateTable(WalkCapsule.__table__).compile(dialect=postgresql.dialect())
    )

    assert "facts JSONB NOT NULL" in analysis_sql
    assert "motion_events JSONB NOT NULL" in analysis_sql
    assert "PRIMARY KEY (analysis_id, paint_fp)" in sheet_sql
    assert "FOREIGN KEY(analysis_id) REFERENCES walk_analyses (id) ON DELETE CASCADE" in sheet_sql
    assert "capabilities JSONB NOT NULL" in capsule_sql
    assert "trail_context JSONB NOT NULL" in capsule_sql
    assert "PRIMARY KEY (analysis_id)" in capsule_sql


def test_init_and_idempotent_migration_define_the_same_storage_boundary() -> None:
    init_sql = (REPO / "db/init/06_walks.sql").read_text(encoding="utf-8")
    migration_sql = (REPO / "db/migrations/2026-09-02_walk_analyses.sql").read_text(
        encoding="utf-8"
    )
    required = {
        "walks_analysis_state_check",
        "CREATE TABLE IF NOT EXISTS walk_analyses",
        "walk_analyses_identity_unique",
        "CREATE TABLE IF NOT EXISTS walk_cellophane_sheets",
        "PRIMARY KEY (analysis_id, paint_fp)",
        "walk_cellophane_paint_fp_idx",
    }

    assert all(token in init_sql for token in required)
    assert all(token in migration_sql for token in required)
    assert "ALTER TABLE walks ADD COLUMN IF NOT EXISTS analysis_state" in migration_sql
    assert "CREATE TABLE IF NOT EXISTS walk_cellophane_cell (" not in init_sql
    assert "CREATE TABLE IF NOT EXISTS walk_cellophane_cell (" not in migration_sql

    capsule_migration = (
        REPO / "db/migrations/2026-09-03_walk_capsules.sql"
    ).read_text(encoding="utf-8")
    capsule_required = {
        "CREATE TABLE IF NOT EXISTS walk_capsules",
        "walk_capsules_versions_positive",
        "walk_capsules_capabilities_array",
        "walk_capsules_context_object",
        "ON CONFLICT (analysis_id) DO NOTHING",
    }
    assert all(token in init_sql for token in capsule_required - {"ON CONFLICT (analysis_id) DO NOTHING"})
    assert all(token in capsule_migration for token in capsule_required)


def test_every_named_model_check_is_present_in_init_sql() -> None:
    init_sql = (REPO / "db/init/06_walks.sql").read_text(encoding="utf-8")
    checks = [
        constraint
        for table in (
            Walk.__table__,
            WalkAnalysis.__table__,
            WalkCellophaneSheet.__table__,
            WalkCapsule.__table__,
        )
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    ]

    assert checks
    assert all(constraint.name in init_sql for constraint in checks)
