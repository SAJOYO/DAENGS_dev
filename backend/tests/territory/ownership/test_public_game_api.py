"""Authenticated public-game contracts and external I/O after releasing the snapshot."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from daengs_backend.config import settings
from daengs_backend.core.database import get_session, get_snapshot_session
from daengs_backend.core.deps import AppPrincipal, current_app_member_token_only
from daengs_backend.core.storage import StorageNotConfiguredError
from daengs_backend.main import app
from daengs_backend.schemas.territory_game import LeaderboardCursor, PublicSitesCursor
from daengs_backend.services import territory_game as service
from daengs_backend.services.activity_core.first_season_policy import Rules
from daengs_backend.services.activity_core.game_policy import POINT_DENOMINATOR
from daengs_backend.services.territory_site_batch_lookup import get_territory_site_batch_lookup
from daengs_backend.services.territory_site_lookup import (
    TerritorySiteSnapshot,
    TerritorySiteUnavailableError,
)

NOW = 1_800_000_000_000
SITE = "territory-site:hex-v1:140:324:777"
ROOT = "/app/territory"


@pytest.fixture
def state(monkeypatch):
    from dataclasses import asdict

    state = SimpleNamespace(
        viewer=uuid.uuid4(),
        pet=uuid.uuid4(),
        exists=True,
        participating=True,
        rolled_back=False,
        lookup_calls=0,
        storage_calls=0,
        query_calls=0,
        missing_location=False,
        lookup_error=False,
        storage_error=False,
        season=SimpleNamespace(
            id="first",
            starts_ms=NOW - 1000,
            ends_ms=NOW + 10_000,
            revision=7,
            confirmed_ms=NOW - 50,
            rules=asdict(Rules()),
        ),
    )
    state.dog = SimpleNamespace(
        pet_id=state.pet,
        name="두부",
        breed="BICHON",
        app_user_id=uuid.uuid4(),
        has_photo=True,
        photo_updated_at=datetime.fromtimestamp(NOW / 1000, UTC),
        photo_storage_key="pets/confirmed-only.jpg",
        photo_generation="generation-1",
        photo_content_type="image/jpeg",
        photo_size_bytes=123,
        total_units=120 * POINT_DENOMINATOR,
        holding_units=0,
        base_points=100,
        takeover_points=20,
        rank=1,
    )
    state.rows = [state.dog]
    state.sites = [
        SimpleNamespace(
            _mapping={
                "site_id": SITE,
                "version": 2,
                "pet_id": state.pet,
                "pet_name": "두부",
                "pet_breed": "BICHON",
                "certification": "VERIFIED",
                "occupied_at": datetime.fromtimestamp(NOW / 1000, UTC),
                "expires_at": None,
            }
        )
    ]

    state.active = True

    async def member(db, member_id):
        assert member_id == state.viewer
        return state.active

    def request_session():
        raise AssertionError("public reads must not keep a second authentication session open")

    async def season(db):
        return state.season

    async def public_pet(db, pet_id, *, photo=False):
        return state.dog if state.exists and pet_id == state.pet else None

    async def leaderboard(db, season, rates, after, limit):
        state.query_calls += 1
        state.after = after
        return len(state.rows), state.rows

    async def standing(db, season, rates, pet_id):
        return state.dog if state.participating else None

    async def counts(db, season_id, now, pets):
        return {state.pet: (2, 1)}

    async def sites(db, owner, season_id, now, pet_id, after, limit):
        assert owner is None and pet_id == state.pet
        state.query_calls += 1
        state.site_after = after
        return len(state.sites), state.sites

    class DB:
        async def rollback(self):
            state.rolled_back = True

    class Lookup:
        async def find_by_ids(self, ids):
            assert state.rolled_back
            state.lookup_calls += 1
            state.lookup_ids = ids
            if state.lookup_error:
                raise TerritorySiteUnavailableError
            return (
                {}
                if state.missing_location
                else {i: TerritorySiteSnapshot(i, 37.5, 127.0) for i in ids}
            )

    class Storage:
        def download_url(self, key, **kwargs):
            assert state.rolled_back
            assert key == "pets/confirmed-only.jpg"
            assert kwargs["generation"] == "generation-1"
            assert kwargs["bridge_download_path"] == service.PET_PHOTO_BRIDGE_DOWNLOAD_PATH
            state.storage_calls += 1
            if state.storage_error:
                raise StorageNotConfiguredError
            return "https://photos.example.test/confirmed.jpg"

    monkeypatch.setattr(settings, "activity_game_enabled", True)
    monkeypatch.setattr(service.activity_game, "now_ms", lambda: NOW)
    monkeypatch.setattr(service.activity_repo, "read_active_season", season)
    monkeypatch.setattr(service.repo, "active_member", member)
    monkeypatch.setattr(service.repo, "public_pet", public_pet)
    monkeypatch.setattr(service.repo, "leaderboard", leaderboard)
    monkeypatch.setattr(service.repo, "standing", standing)
    monkeypatch.setattr(service.repo, "owned_counts", counts)
    monkeypatch.setattr(service.owned_repo, "page", sites)
    monkeypatch.setattr(service, "get_storage", lambda: Storage())
    app.dependency_overrides[get_snapshot_session] = lambda: DB()
    app.dependency_overrides[get_session] = request_session
    app.dependency_overrides[current_app_member_token_only] = lambda: AppPrincipal(
        app_user_id=state.viewer
    )
    app.dependency_overrides[get_territory_site_batch_lookup] = lambda: Lookup()
    yield state
    app.dependency_overrides.clear()


@pytest.fixture
def client(state):
    # Exercise the real assembled routes without starting unrelated runtime services.
    yield TestClient(app)


def urls(state):
    return [ROOT + "/leaderboard"] + [
        f"{ROOT}/pets/{state.pet}/{part}" for part in ("profile", "sites", "photo")
    ]


def test_peer_rank_and_profile_only_expose_game_fields(client, state):
    leaderboard = client.get(urls(state)[0]).json()
    profile = client.get(urls(state)[1]).json()
    assert profile["status"] == "READY"
    assert profile["pet"] == leaderboard["items"][0]["pet"]
    assert profile["season_record"] == leaderboard["items"][0]["season_record"]
    assert not profile["pet"]["is_mine"]
    assert profile["season_record"]["points"] == "120"
    assert profile["season_record"]["owned_site_count"] == 2
    assert set(profile["pet"]) == {
        "pet_id",
        "name",
        "breed",
        "is_mine",
        "has_photo",
        "photo_updated_at",
    }
    assert set(profile["season_record"]) == {
        "rank",
        "points",
        "base_points",
        "takeover_points",
        "holding_points",
        "owned_site_count",
        "verified_site_count",
        "score_as_of_ms",
    }
    assert state.query_calls == 1 and state.storage_calls == 0


def test_all_routes_require_authentication_and_feature_flag(client, state, monkeypatch):
    monkeypatch.setattr(settings, "activity_game_enabled", False)
    for url in urls(state):
        response = client.get(url)
        assert (
            response.status_code == 503 and response.json()["detail"]["code"] == "activity_disabled"
        )
    assert state.query_calls == state.lookup_calls == state.storage_calls == 0
    del app.dependency_overrides[current_app_member_token_only]
    for url in urls(state):
        assert client.get(url).status_code == 401


def test_inactive_member_token_cannot_read_public_data(client, state):
    state.active = False
    for url in urls(state):
        assert client.get(url).status_code == 401
    assert state.query_calls == state.lookup_calls == state.storage_calls == 0


@pytest.mark.parametrize("kind", ["absent", "ended", "future"])
def test_no_active_season_is_explicit(client, state, kind):
    if kind == "absent":
        state.season = None
    elif kind == "ended":
        state.season.ends_ms = NOW
    else:
        state.season.starts_ms = NOW + 1
    for url in urls(state)[:3]:
        body = client.get(url).json()
        assert body["status"] == "NO_ACTIVE_SEASON"
    assert state.query_calls == state.lookup_calls == 0


def test_no_participation_is_not_fake_zero_or_public_registration_directory(client, state):
    state.participating = False
    profile = client.get(urls(state)[1]).json()
    assert profile["status"] == "NOT_PARTICIPATING" and profile["season_record"] is None
    state.exists = False
    for url in urls(state)[1:]:
        assert client.get(url).status_code == 404


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 101}, {"cursor": ""}, {"cursor": "a" * 1025}]
)
def test_pagination_bounds(client, state, params):
    for url in (urls(state)[0], urls(state)[2]):
        assert client.get(url, params=params).status_code == 422
    assert state.query_calls == state.lookup_calls == 0


@pytest.mark.parametrize("kind", ["malformed", "wrong_kind", "season", "revision"])
def test_leaderboard_cursor_validation(client, state, kind):
    value = LeaderboardCursor(
        season="old" if kind == "season" else "first",
        revision=6 if kind == "revision" else 7,
        pet=state.pet,
        units="0",
    )
    if kind == "wrong_kind":
        value = PublicSitesCursor(season="first", pet=state.pet, after=SITE)
    response = client.get(
        urls(state)[0],
        params={"cursor": "%%%" if kind == "malformed" else service.encode_cursor(value)},
    )
    assert response.status_code == (409 if kind in {"season", "revision"} else 400)
    assert state.query_calls == 0


def test_public_sites_cursor_binds_pet_and_season_and_never_discloses_member(client, state):
    state.sites.append(SimpleNamespace(_mapping=state.sites[0]._mapping | {"site_id": SITE + "0"}))
    first = client.get(urls(state)[2], params={"limit": 1}).json()
    after = service.decode_cursor(first["next_cursor"], PublicSitesCursor)
    assert after.pet == state.pet and after.after == SITE
    assert "owner" not in after.model_dump()
    assert state.lookup_ids == [SITE]
    for cursor, code in [
        (after.model_copy(update={"pet": uuid.uuid4()}), 400),
        (after.model_copy(update={"season": "old"}), 409),
    ]:
        assert (
            client.get(urls(state)[2], params={"cursor": service.encode_cursor(cursor)}).status_code
            == code
        )
    state.sites = state.sites[1:]
    assert (
        client.get(urls(state)[2], params={"cursor": first["next_cursor"]}).json()["next_cursor"]
        is None
    )
    assert state.site_after == SITE


def test_missing_location_and_place_failure_preserve_their_meaning(client, state):
    state.missing_location = True
    page = client.get(urls(state)[2]).json()
    assert page["total_count"] == 1 and page["items"][0]["location_status"] == "NOT_FOUND"
    assert page["items"][0]["location"] is None
    state.lookup_error = True
    response = client.get(urls(state)[2])
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "territory_sites_unavailable"


def test_confirmed_profile_photo_without_private_pet_api_access(client, state):
    response = client.get(urls(state)[3])
    assert response.status_code == 200
    assert response.json()["download_url"] == "https://photos.example.test/confirmed.jpg"
    assert set(response.json()) == {"download_url", "content_type", "size_bytes", "updated_at"}
    state.storage_error = True
    assert client.get(urls(state)[3]).status_code == 503
    state.dog.has_photo = False
    response = client.get(urls(state)[3])
    assert response.status_code == 404 and response.json()["detail"]["code"] == "no_photo"
