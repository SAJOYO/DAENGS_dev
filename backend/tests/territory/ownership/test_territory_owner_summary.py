"""Selected owner card: authenticated public projection, not private activity history."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from daengs_backend.config import settings
from daengs_backend.core.database import get_snapshot_session
from daengs_backend.core.deps import AppPrincipal, current_app_user
from daengs_backend.main import app
from daengs_backend.models.activity import ActivityAccount
from daengs_backend.services import activity
from daengs_backend.services import territory_owner as service

SITE = "territory-site:hex-v1:140:324:777"
URL = "/app/territory/owner-summary"
NOW = 1_800_000_000_000


@pytest.fixture
def state(monkeypatch):
    owner_id, pet_id = uuid.uuid4(), uuid.uuid4()
    state = SimpleNamespace(
        row=SimpleNamespace(
            site_id=SITE,
            version=7,
            pet_id=pet_id,
            pet_name="두부",
            app_user_id=owner_id,
            certification="VERIFIED",
            occupied_at=datetime.fromtimestamp((NOW - 5000) / 1000, UTC),
        ),
        season=SimpleNamespace(id="season-A", starts_ms=NOW - 10000, ends_ms=NOW + 10000),
        account=SimpleNamespace(
            score={
                "bonus": 12,
                "holding_units": 54_000_000_000,
                "current_count": 7,
                "last_ms": NOW - 1000,
            }
        ),
    )

    async def sites(db, ids):
        assert ids == [SITE]
        return [state.row] if state.row else []

    async def season(db):
        return state.season

    class DB:
        async def get(self, model, key):
            assert model is ActivityAccount
            assert key == (state.season.id, state.row.pet_id)
            return state.account

    monkeypatch.setattr(settings, "activity_game_enabled", True)
    monkeypatch.setattr(service.territory_repo, "read_sites", sites)
    monkeypatch.setattr(service.activity_repo, "read_active_season", season)
    monkeypatch.setattr(service.activity_game, "now_ms", lambda: NOW)
    state.db = DB()
    return state


@pytest.fixture
def client(state):
    app.dependency_overrides[get_snapshot_session] = lambda: state.db
    app.dependency_overrides[current_app_user] = lambda: AppPrincipal(app_user_id=uuid.uuid4())
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_auth_and_query_validation(client):
    for params in ({}, {"site_id": "bad"}, {"site_id": "x" * 97}):
        assert client.get(URL, params=params).status_code == 422
    del app.dependency_overrides[current_app_user]
    assert client.get(URL, params={"site_id": SITE}).status_code == 401


def test_peer_owner_and_exact_public_fields(client, state):
    response = client.get(URL, params={"site_id": SITE})
    assert response.status_code == 200
    value = response.json()
    assert set(value) == {"site_id", "version", "server_now_ms", "season_id", "status", "owner"}
    assert value["status"] == "READY"
    assert value["version"] == 7 and value["season_id"] == "season-A"
    assert set(value["owner"]) == {
        "pet_id",
        "name",
        "is_mine",
        "certification",
        "occupied_at",
        "season_record",
    }
    assert value["owner"]["pet_id"] == str(state.row.pet_id)
    assert value["owner"]["name"] == "두부" and value["owner"]["is_mine"] is False
    assert value["owner"]["season_record"] == {
        "points": "13.5",
        "owned_site_count": 7,
        "score_as_of_ms": NOW - 1000,
    }


async def test_same_named_dogs_are_keyed_by_id_and_viewer(state):
    first = await service.summary(state.db, state.row.app_user_id, SITE)
    assert first["owner"]["is_mine"] is True
    state.row.pet_id, state.row.app_user_id = uuid.uuid4(), uuid.uuid4()
    state.row.version += 1
    state.account.score["bonus"] = 99
    second = await service.summary(state.db, uuid.uuid4(), SITE)
    assert first["owner"]["name"] == second["owner"]["name"]
    assert first["owner"]["pet_id"] != second["owner"]["pet_id"]
    assert second["owner"]["season_record"]["points"] == "100.5"
    assert second["version"] == first["version"] + 1


def test_missing_account_is_pending_but_known_zero_is_zero(client, state):
    state.account = None
    value = client.get(URL, params={"site_id": SITE}).json()
    assert value["status"] == "PENDING" and value["owner"]["season_record"] is None
    state.account = SimpleNamespace(
        score={
            "bonus": 0,
            "holding_units": 0,
            "current_count": 1,
            "last_ms": NOW,
        }
    )
    value = client.get(URL, params={"site_id": SITE}).json()
    assert value["status"] == "READY"
    assert value["owner"]["season_record"]["points"] == "0"


@pytest.mark.parametrize("season_case", ["absent", "ended", "future"])
def test_no_active_season_never_attaches_old_score(client, state, season_case):
    if season_case == "absent":
        state.season = None
    elif season_case == "ended":
        state.season.ends_ms = NOW
    else:
        state.season.starts_ms = NOW + 1
    value = client.get(URL, params={"site_id": SITE}).json()
    assert value["status"] == "NO_ACTIVE_SEASON" and value["season_id"] is None
    assert value["owner"]["season_record"] is None


@pytest.mark.parametrize("missing_row", [False, True])
def test_unoccupied_does_not_invent_owner(client, state, missing_row):
    if missing_row:
        state.row = None
    else:
        state.row.pet_id = None
    value = client.get(URL, params={"site_id": SITE}).json()
    assert value["owner"] is None and value["status"] == "UNOCCUPIED"
    assert value["version"] == (0 if missing_row else 7)


def test_disabled_is_explicit_503(client, monkeypatch):
    monkeypatch.setattr(settings, "activity_game_enabled", False)
    response = client.get(URL, params={"site_id": SITE})
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "activity_disabled"}}


async def test_disabled_does_not_read_new_tables(monkeypatch):
    monkeypatch.setattr(settings, "activity_game_enabled", False)
    with pytest.raises(activity.ActivityDisabled):
        await service.summary(object(), uuid.uuid4(), SITE)


@pytest.mark.parametrize(
    "holding,expected",
    [
        (0, "4"),
        (1, "4"),
        (3_599_999_999, "4"),
        (3_600_000_000, "4.1"),
        (35_999_999_999, "4.9"),
        (36_000_000_000, "5"),
        (36_000_000_000 * 10**30 + 3_600_000_000, str(10**30 + 4) + ".1"),
    ],
)
def test_points_truncate_without_float_precision_loss(holding, expected):
    assert service.display_points({"bonus": 4, "holding_units": holding}) == expected
