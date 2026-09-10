"""Owner-scoped HTTP contract, bounded pagination and remote coordinate failures."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from daengs_backend.config import settings
from daengs_backend.core.database import get_snapshot_session
from daengs_backend.core.deps import AppPrincipal, current_app_user
from daengs_backend.main import app
from daengs_backend.services import territory_owned as service
from daengs_backend.services.territory_site_batch_lookup import get_territory_site_batch_lookup
from daengs_backend.services.territory_site_lookup import (
    TerritorySiteSnapshot,
    TerritorySiteUnavailableError,
)

URL = "/app/territory/my-sites"
SITE = "territory-site:hex-v1:140:324:777"
NOW = 1_800_000_000_000


@pytest.fixture
def state(monkeypatch):
    state = SimpleNamespace(
        owner=uuid.uuid4(),
        pet=uuid.uuid4(),
        total=1,
        rollback=False,
        season=SimpleNamespace(id="first", starts_ms=NOW - 1, ends_ms=NOW + 10000),
        pet_exists=True,
        location=True,
        lookup_error=False,
        query_calls=0,
        lookup_calls=0,
    )
    state.rows = [
        SimpleNamespace(
            _mapping={
                "site_id": SITE,
                "version": 2,
                "pet_id": state.pet,
                "pet_name": "두부",
                "pet_breed": "BICHON",
                "certification": "UNVERIFIED",
                "occupied_at": datetime.fromtimestamp(NOW / 1000, UTC),
                "expires_at": datetime.fromtimestamp((NOW + 5000) / 1000, UTC),
            }
        )
    ]

    async def season(_db):
        return state.season

    async def pet(_db, owner, pet_id):
        assert owner == state.owner
        return object() if state.pet_exists and pet_id == state.pet else None

    async def page(db, owner, season, now, pet_id, after, limit):
        assert owner == state.owner and season == "first"
        state.query_calls += 1
        state.query = (pet_id, after, limit)
        return state.total, state.rows

    class DB:
        async def rollback(self):
            state.rollback = True

    class Lookup:
        async def find_by_ids(self, ids):
            assert state.rollback, "network must run after releasing the database snapshot"
            state.lookup_calls += 1
            if state.lookup_error:
                raise TerritorySiteUnavailableError
            return (
                {i: TerritorySiteSnapshot(i, Decimal("37.51"), Decimal("127.02")) for i in ids}
                if state.location
                else {}
            )

    monkeypatch.setattr(settings, "activity_game_enabled", True)
    monkeypatch.setattr(service.activity_repo, "read_active_season", season)
    monkeypatch.setattr(service.activity_game, "now_ms", lambda: NOW)
    monkeypatch.setattr(service.pet_repo, "get_owned", pet)
    monkeypatch.setattr(service.repo, "page", page)
    app.dependency_overrides[get_snapshot_session] = lambda: DB()
    app.dependency_overrides[current_app_user] = lambda: AppPrincipal(app_user_id=state.owner)
    app.dependency_overrides[get_territory_site_batch_lookup] = lambda: Lookup()
    yield state
    app.dependency_overrides.clear()


@pytest.fixture
def client(state):
    with TestClient(app) as client:
        yield client


def test_page_returns_coordinates_and_no_private_claim_evidence(client, state):
    response = client.get(URL)
    assert response.status_code == 200
    page = response.json()
    assert page["status"] == "READY" and page["total_count"] == 1
    assert page["pet_id"] is None and page["next_cursor"] is None
    item = page["items"][0]
    assert item["pet_id"] == str(state.pet) and item["pet_name"] == "두부"
    assert item["location"] == {"lat": 37.51, "lng": 127.02}
    assert set(item) == {
        "site_id",
        "version",
        "pet_id",
        "pet_name",
        "pet_breed",
        "certification",
        "occupied_at",
        "expires_at",
        "location",
        "location_status",
    }


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 101},
        {"pet_id": "invalid"},
        {"cursor": ""},
        {"cursor": "a" * 1025},
    ],
)
def test_request_bounds(client, state, params):
    assert client.get(URL, params=params).status_code == 422
    assert state.query_calls == state.lookup_calls == 0


def test_auth_and_pet_owner_checks(client, state):
    assert client.get(URL, params={"pet_id": state.pet}).status_code == 200
    assert state.query[0] == state.pet
    assert client.get(URL, params={"pet_id": uuid.uuid4()}).status_code == 404
    state.pet_exists = False
    assert client.get(URL, params={"pet_id": state.pet}).status_code == 404
    del app.dependency_overrides[current_app_user]
    assert client.get(URL).status_code == 401


@pytest.mark.parametrize("kind", ["invalid", "owner", "pet", "season"])
def test_cursor_is_bound_to_member_filter_and_season(client, state, kind):
    cursor = (
        "%%%"
        if kind == "invalid"
        else service.encode_cursor(
            uuid.uuid4() if kind == "owner" else state.owner,
            state.pet if kind == "pet" else None,
            "old" if kind == "season" else "first",
            SITE,
        )
    )
    response = client.get(URL, params={"cursor": cursor})
    assert response.status_code == (409 if kind == "season" else 400)
    assert state.query_calls == state.lookup_calls == 0


def test_extra_row_becomes_cursor_and_is_not_sent_to_place(client, state):
    second = SimpleNamespace(_mapping=state.rows[0]._mapping | {"site_id": SITE + "0"})
    state.rows.append(second)
    state.total = 2
    page = client.get(URL, params={"limit": 1}).json()
    assert len(page["items"]) == 1 and page["total_count"] == 2
    after = service.decode_cursor(page["next_cursor"], state.owner, None)
    assert after.after == SITE
    state.rows = [second]
    last = client.get(URL, params={"limit": 1, "cursor": page["next_cursor"]}).json()
    assert state.query == (None, SITE, 1)
    assert last["next_cursor"] is None


@pytest.mark.parametrize("kind", ["absent", "ended", "future"])
def test_no_current_season_is_explicit_and_skips_site_queries(client, state, kind):
    if kind == "absent":
        state.season = None
    elif kind == "ended":
        state.season.ends_ms = NOW
    else:
        state.season.starts_ms = NOW + 1
    page = client.get(URL).json()
    assert page["status"] == "NO_ACTIVE_SEASON" and page["season_id"] is None
    assert page["items"] == [] and page["total_count"] == 0
    assert state.query_calls == state.lookup_calls == 0


def test_removed_site_remains_in_owned_count_and_outage_is_not_empty_success(client, state):
    state.location = False
    page = client.get(URL).json()
    assert page["total_count"] == 1 and page["items"][0]["location"] is None
    assert page["items"][0]["location_status"] == "NOT_FOUND"
    state.lookup_error = True
    response = client.get(URL)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "territory_sites_unavailable"


def test_disabled_and_empty_lists_do_not_call_place(client, state, monkeypatch):
    state.rows, state.total = [], 0
    assert client.get(URL).json()["total_count"] == 0
    assert state.lookup_calls == 0
    monkeypatch.setattr(settings, "activity_game_enabled", False)
    assert client.get(URL).json()["detail"]["code"] == "activity_disabled"
