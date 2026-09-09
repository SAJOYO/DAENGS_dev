"""DB 없는 집계/HTTP 계약 회귀. SQL은 PostgreSQL 컴파일까지 확인합니다."""

import uuid
from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB

from daengs_backend.config import settings
from daengs_backend.core.database import get_snapshot_session
from daengs_backend.core.deps import AppPrincipal, CurrentAppUser
from daengs_backend.repositories import walk_behavior_comparison as repo
from daengs_backend.routers.walk_spatial_diary import router
from daengs_backend.schemas.walk_behavior_comparison import BehaviorComparisonRequest
from daengs_backend.services import walk_behavior_comparison as service
from daengs_backend.services import walk_spatial_diary as spatial
from daengs_backend.services.walk_entry import EntryNotFound
from tests.walk.support.behavior_comparison import AT, OWNER, PET, capsule, entry, example

URL = "/app/walks/spatial-diary/behavior-comparisons/query"


def request(**selector):
    return BehaviorComparisonRequest(
        walk_selector={"pet_id": PET, "since": "2026-09-02", "until": "2026-09-02", **selector},
        behavior_code="sniffing",
    )


@pytest.fixture
def repository(monkeypatch):
    """DB 없이 DAO 경계만 교체하고 실제 service 선택·복호·집계를 실행합니다."""
    pairs, rows = example()
    snapshot = object()
    calls = []
    monkeypatch.setattr(settings, "walk_entry_v2_enabled", True)

    async def owned(session, owner, pet):
        assert session is snapshot and owner == OWNER and pet == PET
        return object()

    async def index(session, owner, pet, **kwargs):
        assert session is snapshot and owner == OWNER and pet == PET
        return [pair[0] for pair in pairs][: kwargs["limit"]]

    async def sheets(session, keys):
        assert session is snapshot
        return [stored for _, stored in pairs if (stored.analysis_id, stored.paint_fp) in keys]

    async def entries(session, owner, pet, walk_ids, code, *, limit):
        assert session is snapshot and owner == OWNER and pet == PET
        calls.append((walk_ids, code, limit))
        # Match the DAO contract; the separate PostgreSQL query test checks its filters.
        return [
            row
            for row in rows
            if row.entry.walk_id in walk_ids
            and row.entry.payload is not None
            and row.entry.payload.get("pet_id") == str(pet)
            and row.entry.payload.get("behavior_code") == code
        ][:limit]

    monkeypatch.setattr(spatial.pet_repo, "get_owned", owned)
    monkeypatch.setattr(spatial.diary_repo, "list_capsule_index", index)
    monkeypatch.setattr(spatial.diary_repo, "list_cellophane_sheets", sheets)
    monkeypatch.setattr(repo, "list_behavior_entries", entries)
    return pairs, rows, snapshot, calls


async def test_one_snapshot_selects_positive_mass_walks_and_counts_each_walk_once(repository):
    pairs, rows, snapshot, calls = repository
    # Outside A must not enter B, and undirected/other-pet records are not this dog's records.
    rows.extend(
        [
            entry(3, pairs[2][0].walk_id),
            entry(4, pairs[1][0].walk_id, pet_id=None),
            entry(5, pairs[1][0].walk_id, pet_id=uuid.UUID(int=99)),
        ]
    )
    result = await service.query_comparison(snapshot, OWNER, request())

    assert result.baseline_walk_ids == (pairs[0][0].walk_id, pairs[1][0].walk_id)
    assert result.matching_walk_ids == (pairs[0][0].walk_id,)
    assert result.baseline_field.denominator == 2
    assert result.baseline_field.values == {(0, 0): 0.375, (1, 0): 0.625}
    assert result.matching_field.denominator == 1
    assert result.matching_field.values == {(0, 0): 0.75, (1, 0): 0.25}
    assert result.summary.model_dump() == {
        "selected_walk_count": 3,
        "excluded_empty_walk_count": 1,
        "entry_count": 2,
        "recorded_day_count": 1,
        "unlocated_entry_count": 1,
    }
    assert calls == [(result.baseline_walk_ids, "sniffing", service.MAX_BEHAVIOR_EVIDENCE + 1)]
    assert result.evidence[0].pin["policy_version"] == "legacy-v1"
    assert result.evidence[1].pin["point"] is None


async def test_empty_and_identical_cohorts_are_valid(repository):
    pairs, rows, snapshot, _ = repository
    rows.clear()
    empty = await service.query_comparison(snapshot, OWNER, request())
    assert empty.matching_walk_ids == () and empty.matching_field.denominator == 0
    assert empty.matching_field.values == {}
    rows.extend(entry(i, pair[0].walk_id, located=False) for i, pair in enumerate(pairs))
    same = await service.query_comparison(snapshot, OWNER, request())
    assert same.baseline_walk_ids == same.matching_walk_ids
    assert same.baseline_field.values == same.matching_field.values
    assert same.summary.unlocated_entry_count == 2
    pairs.clear()
    none = await service.query_comparison(snapshot, OWNER, request())
    assert none.baseline_walk_ids == none.matching_walk_ids == ()
    assert none.baseline_field.denominator == 0


async def test_kst_date_and_facets_define_A_before_behavior_lookup(repository):
    pairs, rows, snapshot, _ = repository
    pairs.extend(
        [
            capsule(13, {(3, 0): 1.0}, at=AT - timedelta(hours=1)),
            capsule(14, {(4, 0): 1.0}, weather_code=0),
        ]
    )
    rows.extend([entry(3, pairs[-2][0].walk_id), entry(4, pairs[-1][0].walk_id)])
    result = await service.query_comparison(
        snapshot,
        OWNER,
        request(
            context_facets=[
                {"axis": "precipitation", "values": ["rain"]},
            ]
        ),
    )
    assert result.baseline_walk_ids == (pairs[0][0].walk_id, pairs[1][0].walk_id)
    assert result.summary.selected_walk_count == 3
    assert len(result.evidence) == 2


async def test_pin_revision_content_delete_reassignment_and_reanalysis_refresh_sources(repository):
    pairs, rows, snapshot, _ = repository

    async def query():
        return await service.query_comparison(snapshot, OWNER, request())

    first = await query()
    again = await service.query_comparison(snapshot, OWNER, request(), view_as_of=AT)
    assert first.source_revision == again.source_revision  # Query time is not evidence.
    rows[1].pin.pin_revision += 1
    pin = await query()
    assert pin.source_revision != first.source_revision
    assert pin.matching_walk_ids == first.matching_walk_ids
    rows[0].entry.revision += 1
    content = await query()
    assert content.source_revision != pin.source_revision
    pairs[0] = (replace(pairs[0][0], analysis_id=uuid.UUID(int=999)), pairs[0][1])
    pairs[0][1].analysis_id = uuid.UUID(int=999)
    analysis = await query()
    assert analysis.source_revision != content.source_revision
    pairs[1] = capsule(11, {(1, 0): 10.0})  # Same distribution, different actual source.
    source = await query()
    assert source.source_revision != analysis.source_revision
    rows[0].entry.payload = None
    deleted = await query()
    assert deleted.source_revision != source.source_revision and deleted.summary.entry_count == 1
    rows[1].entry.payload = rows[1].entry.payload | {"pet_id": None}
    reassigned = await query()
    assert reassigned.source_revision != deleted.source_revision
    assert reassigned.matching_walk_ids == ()


async def test_day_count_uses_recorded_at_kst_and_pin_does_not_use_original_location(repository):
    _, rows, snapshot, _ = repository
    rows[1].entry.payload = rows[1].entry.payload | {
        "recorded_at": (AT - timedelta(hours=1)).isoformat(),
        "location": rows[0].entry.payload["location"],
    }
    result = await service.query_comparison(snapshot, OWNER, request())
    assert result.summary.recorded_day_count == 2
    assert result.summary.unlocated_entry_count == 1


async def test_evidence_limit_fails_without_returning_partial_profile(repository, monkeypatch):
    _, _, snapshot, _ = repository
    monkeypatch.setattr(service, "MAX_BEHAVIOR_EVIDENCE", 1)
    with pytest.raises(spatial.SpatialDiaryViewTooLargeError, match="최대 1개") as caught:
        await service.query_comparison(snapshot, OWNER, request())
    assert caught.value.code == "behavior_comparison_evidence_limit"


async def test_disabled_and_foreign_pet_do_not_read_records(repository, monkeypatch):
    _, _, snapshot, calls = repository
    monkeypatch.setattr(settings, "walk_entry_v2_enabled", False)
    with pytest.raises(EntryNotFound):
        await service.query_comparison(snapshot, OWNER, request())
    monkeypatch.setattr(settings, "walk_entry_v2_enabled", True)
    monkeypatch.setattr(spatial.pet_repo, "get_owned", AsyncMock(return_value=None))
    with pytest.raises(spatial.SpatialDiaryPetNotFoundError):
        await service.query_comparison(snapshot, OWNER, request())
    assert calls == []


def test_postgresql_query_scopes_live_behavior_owner_pet_walks_and_limit():
    compiled = repo.behavior_entries_statement(
        OWNER, PET, (uuid.UUID(int=10),), "sniffing", limit=2001
    ).compile(dialect=postgresql.dialect())
    sql = str(compiled)
    params = compiled.params
    assert "walks.app_user_id = %(app_user_id_1)s" in sql
    assert params["app_user_id_1"] == OWNER
    assert "walk_pets.pet_id = %(pet_id_1)s" in sql and params["pet_id_1"] == PET
    assert "walk_entries.walk_id IN (__[POSTCOMPILE_walk_id_2])" in sql
    assert params["walk_id_2"] == [uuid.UUID(int=10)]
    assert "walk_entries.payload IS NOT NULL" in sql
    assert "walk_entries.payload != %(payload_2)s::JSONB" in sql
    assert params["payload_2"] == JSONB.NULL
    for index, key, value in [
        (3, "kind", "behavior"),
        (4, "pet_id", str(PET)),
        (5, "behavior_code", "sniffing"),
    ]:
        assert f"(walk_entries.payload ->> %(payload_{index})s) = %(param_{index - 2})s" in sql
        assert params[f"payload_{index}"] == key and params[f"param_{index - 2}"] == value
    assert "LEFT OUTER JOIN walk_entry_pins" in sql and params["param_4"] == 2001


@pytest.fixture
def client(repository):
    _, _, snapshot, _ = repository
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[next(iter(CurrentAppUser.__metadata__)).dependency] = lambda: (
        AppPrincipal(app_user_id=OWNER)
    )

    async def session():
        yield snapshot

    app.dependency_overrides[get_snapshot_session] = session
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def test_http_contract_returns_current_sources_and_equal_walk_normalization(client):
    response = client.post(URL, json=request().model_dump(mode="json"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["spec"]["comparison_version"] == "walk-behavior-comparison-v1"
    for cohort in ("baseline", "matching"):
        field = body[cohort]["field"]
        assert field["metric"] == "walk_utilization" and field["unit"] == "share"
        assert field["normalization"] == "equal_contributing_walks"
        assert field["denominator"] == len(body[cohort]["walk_ids"])
    assert body["evidence"][0]["client_session_id"] == str(uuid.UUID(int=510))
    assert body["evidence"][1]["pin_revision"] == 2
    assert body["receipt"]["paint_fp"] == body["projection"]["paint_fp"]


@pytest.mark.parametrize(
    "change",
    [
        {"behavior_code": "aggressive"},
        {"comparison_version": "unknown"},
        {"field_metric": "visit_rate"},
    ],
)
def test_http_rejects_unsupported_semantics(client, change):
    response = client.post(URL, json=request().model_dump(mode="json") | change)
    assert response.status_code == 422


def test_http_maps_disabled_missing_owner_and_evidence_limit(client, monkeypatch):
    monkeypatch.setattr(settings, "walk_entry_v2_enabled", False)
    assert client.post(URL, json=request().model_dump(mode="json")).status_code == 404
    monkeypatch.setattr(settings, "walk_entry_v2_enabled", True)
    monkeypatch.setattr(service, "MAX_BEHAVIOR_EVIDENCE", 1)
    response = client.post(URL, json=request().model_dump(mode="json"))
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "behavior_comparison_evidence_limit"


def test_http_without_login_rejects_request_before_service():
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        assert client.post(URL, json=request().model_dump(mode="json")).status_code == 401
