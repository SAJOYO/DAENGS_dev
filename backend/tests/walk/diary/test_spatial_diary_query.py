import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from daengs_backend.core import database
from daengs_backend.core.database import get_session, get_snapshot_session
from daengs_backend.core.deps import AppPrincipal, CurrentAppUser
from daengs_backend.models import WalkCellophaneSheet
from daengs_backend.repositories import walk_spatial_diary as diary_repo
from daengs_backend.routers import walk_spatial_diary as diary_router
from daengs_backend.services.walk_artifacts.cellophane import (
    CELLOPHANE_SHEET_SCHEMA_VERSION,
    cellophane_sheet_fingerprint,
    encode_cellophane,
)
from daengs_backend.services.walk_views import spatial_diary as diary_service
from daengs_walk.capsule import ContextStatus, TrailContextSnapshot
from daengs_walk.cellophane import CANONICAL_PAINT_SPEC, Cellophane, PaintSpec
from daengs_walk.spatial_diary import (
    ContextFacetFilter,
    MixedPaintGenerationError,
    SpatialDiaryViewSpec,
    WalkSelector,
)

OWNER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
PET_ID = uuid.UUID("21111111-1111-1111-1111-111111111111")
STARTED_AT = datetime(2026, 9, 1, 9, tzinfo=UTC)


def _context(
    walk_id: uuid.UUID,
    *,
    walked_at: datetime = STARTED_AT,
    weather_code: int | None,
    is_day: bool | None,
    precipitation_kind: str | None = None,
) -> TrailContextSnapshot:
    observed = weather_code is not None or is_day is not None or precipitation_kind is not None
    return TrailContextSnapshot(
        walk_id=walk_id,
        status=ContextStatus.PARTIAL if observed else ContextStatus.UNKNOWN,
        walked_at=walked_at,
        captured_at=walked_at,
        provider="fixture" if observed else None,
        weather_code=weather_code,
        is_day=is_day,
        precipitation_kind=precipitation_kind,
    )


def _index(
    *,
    at: datetime = STARTED_AT,
    weather_code: int | None = 61,
    is_day: bool | None = False,
    precipitation_kind: str | None = None,
    paint_spec: PaintSpec = CANONICAL_PAINT_SPEC,
    cell_count: int = 1,
) -> diary_repo.SpatialDiaryIndexRow:
    walk_id = uuid.uuid4()
    context = _context(
        walk_id,
        walked_at=at,
        weather_code=weather_code,
        is_day=is_day,
        precipitation_kind=precipitation_kind,
    )
    return diary_repo.SpatialDiaryIndexRow(
        analysis_id=uuid.uuid4(),
        walk_id=walk_id,
        started_at=at,
        capsule_version=1,
        context_version=context.context_version,
        trail_context=context.model_dump(mode="json"),
        sheet_schema_version=CELLOPHANE_SHEET_SCHEMA_VERSION,
        paint_version=paint_spec.paint_version,
        grid_version=paint_spec.grid_version,
        radius_u=paint_spec.radius_u,
        profile=paint_spec.profile_name,
        profile_fp=paint_spec.profile_fp,
        sample_step_m=paint_spec.sample_step_m,
        paint_fp=paint_spec.fingerprint,
        cell_count=cell_count,
    )


def _stored_sheet(
    index: diary_repo.SpatialDiaryIndexRow,
    occupancy: dict[tuple[int, int], float],
) -> WalkCellophaneSheet:
    assert index.paint_fp is not None
    sheet = Cellophane(
        walk_id=index.walk_id,
        at=index.started_at,
        radius_u=float(index.radius_u),
        profile=str(index.profile),
        occupancy=occupancy,
        peak={cell: 1.0 for cell in occupancy},
        paint_version=int(index.paint_version),
        grid_version=str(index.grid_version),
        profile_fp=str(index.profile_fp),
        sample_step_m=float(index.sample_step_m),
        paint_fp=index.paint_fp,
    )
    payload = encode_cellophane(sheet)
    return WalkCellophaneSheet(
        analysis_id=index.analysis_id,
        paint_fp=index.paint_fp,
        sheet_schema_version=CELLOPHANE_SHEET_SCHEMA_VERSION,
        paint_version=sheet.paint_version,
        grid_version=sheet.grid_version,
        radius_u=sheet.radius_u,
        profile=sheet.profile,
        profile_fp=sheet.profile_fp,
        sample_step_m=sheet.sample_step_m,
        cell_count=len(occupancy),
        sheet_fingerprint=cellophane_sheet_fingerprint(payload),
        payload=payload,
    )


def _spec(*facets: ContextFacetFilter, metric="visit_rate") -> SpatialDiaryViewSpec:
    return SpatialDiaryViewSpec(
        walk_selector=WalkSelector(
            pet_id=PET_ID,
            since=date(2026, 9, 1),
            until=date(2026, 9, 3),
            context_facets=facets,
        ),
        field_metric=metric,
    )


def _install_repository(
    monkeypatch: pytest.MonkeyPatch,
    index: list[diary_repo.SpatialDiaryIndexRow],
    sheets: list[WalkCellophaneSheet],
    *,
    pet_exists: bool = True,
    total_capsules: int | None = None,
) -> None:
    async def get_owned(session, app_user_id, pet_id, *, for_update=False):
        assert app_user_id == OWNER_ID
        assert pet_id == PET_ID
        return object() if pet_exists else None

    async def count(session, app_user_id, pet_id):
        return len(index) if total_capsules is None else total_capsules

    async def list_index(session, app_user_id, pet_id, **kwargs):
        assert kwargs["limit"] == diary_service.MAX_CANDIDATE_CAPSULES + 1
        return index

    async def list_sheets(session, keys):
        wanted = set(keys)
        return [item for item in sheets if (item.analysis_id, item.paint_fp) in wanted]

    monkeypatch.setattr(diary_service.pet_repo, "get_owned", get_owned)
    monkeypatch.setattr(diary_service.diary_repo, "count_capsules_for_pet", count)
    monkeypatch.setattr(diary_service.diary_repo, "list_capsule_index", list_index)
    monkeypatch.setattr(diary_service.diary_repo, "list_cellophane_sheets", list_sheets)


@pytest.mark.asyncio
async def test_query_selects_pet_date_and_context_then_builds_one_receipt(monkeypatch):
    rain = _index(weather_code=61, is_day=False)
    dry = _index(at=STARTED_AT + timedelta(days=1), weather_code=0, is_day=True)
    unknown = _index(at=STARTED_AT + timedelta(days=2), weather_code=None, is_day=None)
    stored = [
        _stored_sheet(rain, {(0, 0): 3.0}),
        _stored_sheet(dry, {(1, 0): 2.0}),
        _stored_sheet(unknown, {}),
    ]
    _install_repository(monkeypatch, [rain, dry, unknown], stored, total_capsules=4)
    spec = _spec(
        ContextFacetFilter(axis="precipitation", values=("rain",)),
        ContextFacetFilter(axis="daylight", values=("night",)),
    )

    result = await diary_service.query_view(
        object(),
        OWNER_ID,
        spec,
        view_as_of=STARTED_AT,
    )

    assert result.field.values == {(0, 0): 1.0}
    assert result.receipt.total_capsules == 4
    assert result.receipt.selected_capsules == 1
    assert result.receipt.contributing_capsules == 1
    assert result.receipt.context_known_count == 1
    assert result.receipt.context_unknown_count == 0


@pytest.mark.asyncio
async def test_query_uses_kma_precipitation_before_conflicting_wmo(monkeypatch):
    observed_dry = _index(weather_code=61, precipitation_kind="none")
    _install_repository(
        monkeypatch,
        [observed_dry],
        [_stored_sheet(observed_dry, {(0, 0): 1.0})],
    )
    spec = _spec(ContextFacetFilter(axis="precipitation", values=("dry",)))

    result = await diary_service.query_view(
        object(),
        OWNER_ID,
        spec,
        view_as_of=STARTED_AT,
    )

    assert result.receipt.selected_capsules == 1
    assert result.receipt.context_known_count == 1


@pytest.mark.asyncio
async def test_query_rejects_foreign_pet_without_reading_capsules(monkeypatch):
    _install_repository(monkeypatch, [], [], pet_exists=False)

    with pytest.raises(diary_service.SpatialDiaryPetNotFoundError):
        await diary_service.query_view(object(), OWNER_ID, _spec())


@pytest.mark.asyncio
async def test_query_keeps_empty_cohort_as_zero_denominator(monkeypatch):
    _install_repository(monkeypatch, [], [], total_capsules=2)

    result = await diary_service.query_view(object(), OWNER_ID, _spec())

    assert result.field.values == {}
    assert result.field.denominator == 0
    assert result.receipt.total_capsules == 2
    assert result.receipt.selected_capsules == 0
    assert result.receipt.paint_fp == CANONICAL_PAINT_SPEC.fingerprint


@pytest.mark.asyncio
async def test_unknown_context_is_selectable_but_counted_unknown(monkeypatch):
    unknown = _index(weather_code=None, is_day=None, cell_count=0)
    _install_repository(monkeypatch, [unknown], [_stored_sheet(unknown, {})])
    spec = _spec(ContextFacetFilter(axis="precipitation", values=("unknown",)))

    result = await diary_service.query_view(object(), OWNER_ID, spec)

    assert result.receipt.selected_capsules == 1
    assert result.receipt.context_known_count == 0
    assert result.receipt.context_unknown_count == 1


@pytest.mark.asyncio
async def test_query_rejects_mixed_paint_before_loading_payload(monkeypatch):
    other = PaintSpec(
        paint_version=CANONICAL_PAINT_SPEC.paint_version + 1,
        grid_version=CANONICAL_PAINT_SPEC.grid_version,
        radius_u=CANONICAL_PAINT_SPEC.radius_u,
        profile_name=CANONICAL_PAINT_SPEC.profile_name,
        profile_fp=CANONICAL_PAINT_SPEC.profile_fp,
        sample_step_m=CANONICAL_PAINT_SPEC.sample_step_m,
    )
    rows = [_index(), _index(paint_spec=other)]
    _install_repository(monkeypatch, rows, [])

    with pytest.raises(MixedPaintGenerationError):
        await diary_service.query_view(object(), OWNER_ID, _spec())


@pytest.mark.asyncio
async def test_query_accepts_one_homogeneous_noncurrent_paint_generation(monkeypatch):
    other = PaintSpec(
        paint_version=CANONICAL_PAINT_SPEC.paint_version + 1,
        grid_version=CANONICAL_PAINT_SPEC.grid_version,
        radius_u=CANONICAL_PAINT_SPEC.radius_u,
        profile_name=CANONICAL_PAINT_SPEC.profile_name,
        profile_fp=CANONICAL_PAINT_SPEC.profile_fp,
        sample_step_m=CANONICAL_PAINT_SPEC.sample_step_m,
    )
    row = _index(paint_spec=other)
    _install_repository(monkeypatch, [row], [_stored_sheet(row, {(0, 0): 1.0})])

    result = await diary_service.query_view(object(), OWNER_ID, _spec())

    assert result.field.paint_fp == other.fingerprint
    assert result.receipt.paint_fp == other.fingerprint


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("limit_name", "rows", "expected_code"),
    [
        (
            "MAX_CANDIDATE_CAPSULES",
            [_index(), _index()],
            "spatial_diary_candidate_limit",
        ),
        ("MAX_SELECTED_CAPSULES", [_index(), _index()], "spatial_diary_selected_limit"),
        ("MAX_RAW_CELLS", [_index(cell_count=2)], "spatial_diary_raw_cell_limit"),
    ],
)
async def test_query_rejects_oversized_work_before_loading_payload(
    monkeypatch,
    limit_name,
    rows,
    expected_code,
):
    _install_repository(monkeypatch, rows, [])
    monkeypatch.setattr(diary_service, limit_name, 1)

    with pytest.raises(diary_service.SpatialDiaryViewTooLargeError) as caught:
        await diary_service.query_view(object(), OWNER_ID, _spec())

    assert caught.value.code == expected_code


@pytest.mark.asyncio
async def test_query_rejects_missing_or_tampered_selected_sheet(monkeypatch):
    row = _index()
    _install_repository(monkeypatch, [row], [])

    with pytest.raises(diary_service.IncompleteSpatialDiaryCapsuleError):
        await diary_service.query_view(object(), OWNER_ID, _spec())


def test_index_rejects_context_from_another_walk_time():
    row = _index()
    context = row.trail_context | {"walked_at": (row.started_at + timedelta(minutes=1)).isoformat()}

    with pytest.raises(diary_service.IncompleteSpatialDiaryCapsuleError):
        diary_service._decode_index(replace(row, trail_context=context))


@pytest.mark.asyncio
async def test_query_rejects_sheet_from_another_walk_time(monkeypatch):
    row = _index()
    stored = _stored_sheet(row, {(0, 0): 1.0})
    stored.payload = stored.payload | {"at": (row.started_at + timedelta(minutes=1)).isoformat()}
    stored.sheet_fingerprint = cellophane_sheet_fingerprint(stored.payload)
    _install_repository(monkeypatch, [row], [stored])

    with pytest.raises(diary_service.IncompleteSpatialDiaryCapsuleError):
        await diary_service.query_view(object(), OWNER_ID, _spec())


@pytest.mark.asyncio
async def test_query_caps_result_cells(monkeypatch):
    row = _index()
    _install_repository(monkeypatch, [row], [_stored_sheet(row, {(0, 0): 1.0})])
    monkeypatch.setattr(diary_service, "MAX_RESULT_CELLS", 0)

    with pytest.raises(diary_service.SpatialDiaryViewTooLargeError) as caught:
        await diary_service.query_view(object(), OWNER_ID, _spec())

    assert caught.value.code == "spatial_diary_result_cell_limit"


def test_repository_index_query_scopes_owner_pet_kst_dates_and_limit():
    stmt = diary_repo.capsule_index_statement(
        OWNER_ID,
        PET_ID,
        since=date(2026, 9, 1),
        until=date(2026, 9, 3),
        limit=2_001,
    )
    compiled = stmt.compile(dialect=postgresql.dialect())
    sql = str(compiled)

    assert "walk_pets" in sql
    assert "walks.app_user_id" in sql
    assert "walk_pets.pet_id" in sql
    assert "CAST(timezone(" in sql
    assert "Asia/Seoul" in compiled.params.values()
    assert "walk_cellophane_sheets" in sql and "LEFT OUTER JOIN" in sql
    assert "row_number() OVER (PARTITION BY walks.id" in sql
    assert "walk_capsules.sealed_at DESC" in sql
    assert "walk_cellophane_sheets.derived_at DESC NULLS LAST" in sql
    assert "representative_rank" in sql
    assert 2_001 in compiled.params.values()


def test_repository_total_counts_unique_walks_instead_of_analysis_generations():
    stmt = diary_repo.capsule_count_statement(OWNER_ID, PET_ID)
    compiled = stmt.compile(dialect=postgresql.dialect())
    sql = str(compiled)

    assert "count(distinct(walks.id))" in sql.lower()
    assert "walks.app_user_id" in sql
    assert "walk_pets.pet_id" in sql


@pytest.mark.asyncio
async def test_snapshot_dependency_opens_read_only_repeatable_read_session(monkeypatch):
    bind = database.SnapshotSessionLocal.kw["bind"]
    assert bind.sync_engine._execution_options["isolation_level"] == "REPEATABLE READ"
    session = AsyncMock()

    class SessionContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    monkeypatch.setattr(database, "SnapshotSessionLocal", SessionContext)
    dependency = database.get_snapshot_session()

    assert await anext(dependency) is session
    assert str(session.execute.await_args.args[0]) == "SET TRANSACTION READ ONLY"
    await dependency.aclose()


@pytest.fixture
def api_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    app = FastAPI()
    app.include_router(diary_router.router)
    app.dependency_overrides[next(iter(CurrentAppUser.__metadata__)).dependency] = lambda: (
        AppPrincipal(app_user_id=OWNER_ID)
    )

    async def snapshot_session():
        yield object()

    app.dependency_overrides[get_snapshot_session] = snapshot_session
    return TestClient(app, raise_server_exceptions=False)


def _request() -> dict:
    return {
        "walk_selector": {
            "pet_id": str(PET_ID),
            "since": "2026-09-01",
            "until": "2026-09-03",
            "context_facets": [
                {"axis": "precipitation", "values": ["rain"]},
            ],
        },
        "field_metric": "visit_rate",
    }


def test_api_returns_projection_cells_and_named_denominators(api_client, monkeypatch):
    row = _index()
    stored = _stored_sheet(row, {(0, 0): 3.0})
    _install_repository(monkeypatch, [row], [stored])

    response = api_client.post("/app/walks/spatial-diary/views/query", json=_request())

    assert response.status_code == 200
    body = response.json()
    assert body["spec"]["view_version"] == 1
    assert body["spec"]["walk_selector"]["pet_id"] == str(PET_ID)
    assert body["spec"]["walk_selector"]["context_facets"] == [
        {"axis": "precipitation", "values": ["rain"], "policy_version": 2}
    ]
    assert body["projection"]["paint_fp"] == CANONICAL_PAINT_SPEC.fingerprint
    assert body["field"] == {
        "metric": "visit_rate",
        "unit": "ratio",
        "normalization": "selected_walks",
        "denominator": 1.0,
        "cells": [{"q": 0, "r": 0, "value": 1.0, "numerator": 1.0}],
    }
    assert body["receipt"]["selected_capsules"] == 1


def test_api_maps_missing_pet_and_limits_without_leaking_storage_detail(api_client, monkeypatch):
    async def missing(*args, **kwargs):
        raise diary_service.SpatialDiaryPetNotFoundError

    monkeypatch.setattr(diary_service, "query_view", missing)
    missing_response = api_client.post(
        "/app/walks/spatial-diary/views/query",
        json=_request(),
    )
    assert missing_response.status_code == 404

    async def too_large(*args, **kwargs):
        raise diary_service.SpatialDiaryViewTooLargeError("too_many", "필터를 좁혀 주세요.")

    monkeypatch.setattr(diary_service, "query_view", too_large)
    large_response = api_client.post(
        "/app/walks/spatial-diary/views/query",
        json=_request(),
    )
    assert large_response.status_code == 413
    assert large_response.json()["detail"]["code"] == "too_many"


def test_api_logs_incomplete_capsule_without_exposing_storage_detail(api_client, monkeypatch):
    async def incomplete(*args, **kwargs):
        raise diary_service.IncompleteSpatialDiaryCapsuleError("raw storage detail")

    log_exception = Mock()
    monkeypatch.setattr(diary_service, "query_view", incomplete)
    monkeypatch.setattr(diary_router.logger, "exception", log_exception)

    response = api_client.post(
        "/app/walks/spatial-diary/views/query",
        json=_request(),
    )

    assert response.status_code == 500
    assert response.json()["detail"] == {
        "code": "spatial_diary_capsule_incomplete",
        "message": "봉인된 산책 원판을 읽을 수 없습니다.",
    }
    log_exception.assert_called_once()
    assert "raw storage detail" not in response.text


def test_api_rejects_invalid_selector_before_service(api_client):
    request = _request()
    request["walk_selector"]["since"] = "2026-09-04"

    response = api_client.post("/app/walks/spatial-diary/views/query", json=request)

    assert response.status_code == 422


SHEETS_PATH = "/app/walks/spatial-diary/sheets/query"


def _install_record_repository(monkeypatch, owned, indexes, sheets):
    sessions = []

    async def ids(session, owner, requested):
        assert owner == OWNER_ID
        sessions.append(session)
        return {client: walk for client, walk in owned.items() if client in requested}

    async def index(session, owner, walk_ids):
        assert owner == OWNER_ID
        assert set(walk_ids) == set(owned.values())
        assert session is sessions[0]
        return indexes

    async def payloads(session, keys):
        assert session is sessions[0]
        return [sheet for sheet in sheets if (sheet.analysis_id, sheet.paint_fp) in keys]

    monkeypatch.setattr(diary_repo, "list_owned_record_ids", ids)
    monkeypatch.setattr(diary_repo, "list_record_capsule_index", index)
    monkeypatch.setattr(diary_repo, "list_cellophane_sheets", payloads)


def test_record_sheet_api_preserves_order_native_payload_and_all_availability_states(
    api_client,
    monkeypatch,
):
    clients = tuple(uuid.UUID(int=i) for i in range(101, 106))
    # Empty sheets are ready; independent sheets may have different paint generations.
    first = _index(cell_count=2)
    empty = _index(cell_count=0, paint_spec=replace(CANONICAL_PAINT_SPEC, radius_u=4.0))
    first_sheet = _stored_sheet(first, {(1, 0): 4.5, (0, 0): 0.0})
    empty_sheet = _stored_sheet(empty, {})
    pending_walk = uuid.uuid4()
    owned = {clients[0]: first.walk_id, clients[1]: empty.walk_id, clients[2]: pending_walk}
    _install_record_repository(monkeypatch, owned, [empty, first], [first_sheet, empty_sheet])
    requested = (clients[3], clients[0], clients[2], clients[1], clients[4])

    response = api_client.post(
        SHEETS_PATH,
        json={
            "client_session_ids": [str(value) for value in requested],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == 1
    items = body["items"]
    assert [item["client_session_id"] for item in items] == [str(v) for v in requested]
    assert [item["status"] for item in items] == [
        "unavailable",
        "ready",
        "pending",
        "ready",
        "unavailable",
    ]
    for item in (items[0], items[4]):
        assert all(
            item[key] is None
            for key in (
                "walk_id",
                "analysis_id",
                "sheet_fingerprint",
                "sheet",
            )
        )
    assert items[2] == {
        "client_session_id": str(clients[2]),
        "walk_id": str(pending_walk),
        "status": "pending",
        "analysis_id": None,
        "sheet_fingerprint": None,
        "sheet": None,
    }
    for item, stored in ((items[1], first_sheet), (items[3], empty_sheet)):
        assert item["analysis_id"] == str(stored.analysis_id)
        assert item["sheet_fingerprint"] == cellophane_sheet_fingerprint(item["sheet"])
        assert item["sheet"] == stored.payload
    assert items[1]["sheet"]["cols"] == ["q", "r", "occupancy_s", "peak"]
    assert items[1]["sheet"]["cells"][0] == [0, 0, 0.0, 1.0]
    assert items[3]["sheet"]["cells"] == []


@pytest.mark.parametrize(
    "ids",
    [
        [],
        ["not-a-uuid"],
        [str(OWNER_ID), str(OWNER_ID)],
        [str(uuid.UUID(int=i)) for i in range(401)],
    ],
)
def test_record_sheet_request_rejects_invalid_or_duplicate_ids_before_service(
    api_client,
    monkeypatch,
    ids,
):
    query = AsyncMock()
    monkeypatch.setattr(diary_service, "query_record_sheets", query)
    response = api_client.post(SHEETS_PATH, json={"client_session_ids": ids})
    assert response.status_code == 422
    query.assert_not_awaited()


def test_record_sheet_api_requires_member_auth_before_snapshot_query(api_client, monkeypatch):
    app = api_client.app
    app.dependency_overrides.pop(next(iter(CurrentAppUser.__metadata__)).dependency)

    async def no_database():
        yield object()

    app.dependency_overrides[get_session] = no_database
    query = AsyncMock()
    monkeypatch.setattr(diary_service, "query_record_sheets", query)
    response = api_client.post(SHEETS_PATH, json={"client_session_ids": [str(OWNER_ID)]})
    assert response.status_code == 401
    query.assert_not_awaited()


def test_record_sheet_api_limits_cells_before_loading_any_payload(api_client, monkeypatch):
    client_id = uuid.uuid4()
    row = _index(cell_count=diary_service.MAX_RAW_CELLS + 1)
    _install_record_repository(monkeypatch, {client_id: row.walk_id}, [row], [])
    payloads = AsyncMock()
    monkeypatch.setattr(diary_repo, "list_cellophane_sheets", payloads)

    response = api_client.post(SHEETS_PATH, json={"client_session_ids": [str(client_id)]})

    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "spatial_diary_raw_cell_limit"
    payloads.assert_not_awaited()


@pytest.mark.parametrize(
    "damage",
    [
        "sealed_without_sheet",
        "missing_payload",
        "fingerprint",
        "metadata",
        "identity",
        "index_count",
    ],
)
def test_record_sheet_api_rejects_corrupt_sealed_original_without_pending_or_fallback(
    api_client,
    monkeypatch,
    damage,
):
    client_id = uuid.uuid4()
    row = _index()
    stored = _stored_sheet(row, {(0, 0): 1.0})
    sheets = [stored]
    if damage == "sealed_without_sheet":
        row = replace(row, paint_fp=None, cell_count=None)
        sheets = []
    elif damage == "missing_payload":
        sheets = []
    elif damage == "fingerprint":
        stored.payload["cells"][0][2] = 9.0
    elif damage == "metadata":
        stored.profile = "different profile"
    elif damage == "identity":
        stored.payload["walk_id"] = str(uuid.uuid4())
        stored.sheet_fingerprint = cellophane_sheet_fingerprint(stored.payload)
    elif damage == "index_count":
        row = replace(row, cell_count=0)
    _install_record_repository(monkeypatch, {client_id: row.walk_id}, [row], sheets)
    logged = Mock()
    monkeypatch.setattr(diary_router.logger, "exception", logged)

    response = api_client.post(SHEETS_PATH, json={"client_session_ids": [str(client_id)]})

    assert response.status_code == 500
    assert response.json()["detail"] == {
        "code": "spatial_diary_capsule_incomplete",
        "message": "봉인된 산책 원판을 읽을 수 없습니다.",
    }
    assert str(row.walk_id) not in response.text
    logged.assert_called_once()


async def test_record_id_lookup_scopes_owner_and_explicit_ids_without_loading_gps():
    client_id, walk_id = uuid.uuid4(), uuid.uuid4()
    session = AsyncMock()
    session.execute.return_value = Mock(all=Mock(return_value=[(client_id, walk_id)]))

    assert await diary_repo.list_owned_record_ids(session, OWNER_ID, (client_id,)) == {
        client_id: walk_id,
    }
    stmt = session.execute.await_args.args[0]
    compiled = stmt.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "walks.app_user_id =" in sql
    assert "walks.client_session_id IN" in sql
    assert OWNER_ID in compiled.params.values()
    assert [client_id] in compiled.params.values()
    assert "walk_points" not in sql and "walk_pets" not in sql
    assert not stmt._with_options


async def test_record_index_reuses_latest_sealed_selection_without_pet_or_date_reselection():
    walk_id = uuid.uuid4()
    session = AsyncMock()
    session.execute.return_value = Mock(all=Mock(return_value=[]))
    assert await diary_repo.list_record_capsule_index(session, OWNER_ID, ()) == []
    session.execute.assert_not_awaited()

    await diary_repo.list_record_capsule_index(session, OWNER_ID, (walk_id,))

    stmt = session.execute.await_args.args[0]
    compiled = stmt.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "walks.app_user_id =" in sql and "walks.id IN" in sql
    assert OWNER_ID in compiled.params.values()
    assert [walk_id] in compiled.params.values()
    assert "walk_pets" not in sql and "timezone" not in sql and "walk_points" not in sql
    assert "LEFT OUTER JOIN walk_cellophane_sheets" in sql
    assert "row_number() OVER (PARTITION BY walks.id" in sql
    assert (
        "walk_capsules.sealed_at DESC, walk_analyses.derived_at DESC, walk_analyses.id DESC" in sql
    )
    assert "walk_cellophane_sheets.derived_at DESC NULLS LAST" in sql
    assert "walk_cellophane_sheets.paint_fp DESC NULLS LAST" in sql
    assert "representative_rank =" in sql
    assert 1 in compiled.params.values()
