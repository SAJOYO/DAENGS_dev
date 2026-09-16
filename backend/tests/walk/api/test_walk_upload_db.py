"""Initial upload races using real PostgreSQL transactions and HTTP serialization.

Uses the existing disposable localhost/claims_test fixture, never app DB settings.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError

from daengs_backend.config import settings
from daengs_backend.core.database import get_session
from daengs_backend.core.deps import AppPrincipal, current_app_user
from daengs_backend.models import Walk, WalkPet, WalkPointChunk
from daengs_backend.models.activity import ActivitySessionLink
from daengs_backend.repositories import walk as repo
from daengs_backend.routers import walk as router
from daengs_backend.schemas.walk import WalkUpload
from daengs_backend.services import activity
from daengs_backend.services.walk_session import lifecycle as service
from tests.territory.support.ownership import begin


def body(client_id, pets):
    started = datetime(2026, 9, 12, 3, tzinfo=UTC)
    return WalkUpload(
        client_session_id=client_id,
        pet_ids=pets,
        started_at=started,
        ended_at=started + timedelta(minutes=2),
        weather_code=61,
        points=[
            {
                "client_seq": seq,
                "chain_index": 0,
                "at": started + timedelta(seconds=seq * 3),
                "lat": "37.5",
                "lng": str(127 + seq / 10000),
                "recording_eligible": True,
            }
            for seq in (1, 0)
        ],
    )


def http_client(factory, owner):
    app = FastAPI()
    app.include_router(router.router)

    async def session():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[current_app_user] = lambda: AppPrincipal(app_user_id=owner)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


@pytest.mark.parametrize("game_enabled", [False, True])
@pytest.mark.parametrize("different_payload", [False, True])
async def test_concurrent_first_uploads_return_one_complete_walk(
    database, actors, monkeypatch, game_enabled, different_payload
):
    (owner, _), (pet1, pet2, foreign_pet) = actors
    monkeypatch.setattr(settings, "activity_game_enabled", game_enabled)
    client_id = uuid.uuid4()
    if game_enabled:
        await begin(database, owner, [pet1, pet2], client_id=client_id)
    first = body(client_id, [pet2, foreign_pet, pet1])
    second = first.model_copy(deep=True)
    if different_payload:
        second.weather_code = 62
        second.pet_ids = [pet2]
        second.points[0].lng += 1

    original_lookup = repo.get_by_client_session
    both_missing = asyncio.Barrier(2)
    misses = 0

    async def lookup(db, app_user_id, client_session_id):
        nonlocal misses
        result = await original_lookup(db, app_user_id, client_session_id)
        if result is None:
            misses += 1
            if not game_enabled:
                # Force the check-then-insert race, without changing the DB's INSERT/commit.
                await asyncio.wait_for(both_missing.wait(), timeout=5)
        return result

    monkeypatch.setattr(repo, "get_by_client_session", lookup)
    async with http_client(database, owner) as http:
        responses = await asyncio.wait_for(
            asyncio.gather(
                http.post("/app/walks", json=first.model_dump(mode="json")),
                http.post("/app/walks", json=second.model_dump(mode="json")),
            ),
            timeout=15,
        )

    assert sorted(response.status_code for response in responses) == [200, 201]
    assert responses[0].json() == responses[1].json()
    data = responses[0].json()
    winner = first if responses[0].status_code == 201 else second
    expected_pets = sorted(set(winner.pet_ids) - {foreign_pet})
    assert data["pet_ids"] == [str(pet) for pet in expected_pets]
    assert data["weather_code"] == winner.weather_code
    assert [point["client_seq"] for point in data["points"]] == [0, 1]
    assert misses == (1 if game_enabled else 2)
    walk_id = uuid.UUID(data["id"])

    async with database() as db:
        assert await db.scalar(select(func.count()).select_from(Walk)) == 1
        assert await db.scalar(select(func.count()).select_from(WalkPet)) == len(expected_pets)
        assert await db.scalar(select(func.count()).select_from(WalkPointChunk)) == 1
        assert await db.scalar(select(WalkPointChunk.point_count)) == 2
        saved = await repo.get_by_client_session(db, owner, client_id)
        assert router._to_detail(saved).model_dump(mode="json") == data
        links = list(await db.scalars(select(ActivitySessionLink)))
        assert len(links) == int(game_enabled)
        if game_enabled:
            assert links[0].walk_id == walk_id
            assert links[0].game_session_id is not None


@pytest.mark.parametrize("game_enabled", [False, True])
async def test_same_client_session_id_is_scoped_to_owner(
    database, actors, monkeypatch, game_enabled
):
    (owner, other), (pet, _, other_pet) = actors
    monkeypatch.setattr(settings, "activity_game_enabled", game_enabled)
    client_id = uuid.uuid4()
    async with http_client(database, owner) as first, http_client(database, other) as second:
        responses = await asyncio.gather(
            first.post("/app/walks", json=body(client_id, [pet]).model_dump(mode="json")),
            second.post("/app/walks", json=body(client_id, [other_pet]).model_dump(mode="json")),
        )
    assert [response.status_code for response in responses] == [201, 201]
    assert responses[0].json()["id"] != responses[1].json()["id"]
    assert responses[0].json()["pet_ids"] == [str(pet)]
    assert responses[1].json()["pet_ids"] == [str(other_pet)]


@pytest.mark.parametrize("game_enabled", [False, True])
@pytest.mark.parametrize("failure", ["different_unique", "check"])
async def test_unrelated_integrity_failure_rolls_back_and_propagates(
    database, actors, monkeypatch, game_enabled, failure
):
    (owner, _), (pet, _, _) = actors
    monkeypatch.setattr(settings, "activity_game_enabled", game_enabled)
    original_add = repo.add
    lookup_count = 0
    original_lookup = repo.get_by_client_session

    def invalid_add(db, walk):
        if failure == "different_unique":
            walk.pets.append(WalkPet(pet_id=pet))
        else:
            walk.weather_code = 100  # Bypass request validation to exercise a real DB error.
        return original_add(db, walk)

    async def lookup(*args):
        nonlocal lookup_count
        lookup_count += 1
        return await original_lookup(*args)

    monkeypatch.setattr(repo, "add", invalid_add)
    monkeypatch.setattr(repo, "get_by_client_session", lookup)
    async with database() as db:
        with pytest.raises(IntegrityError) as failure_info:
            await service.upload_walk(db, owner, body(uuid.uuid4(), [pet]))
        error = failure_info.value.orig.__cause__
        assert error.sqlstate == ("23505" if failure == "different_unique" else "23514")
        assert error.constraint_name != "walks_client_session_unique"
        assert lookup_count == 1  # Other constraints must not trigger a recovery lookup.
        assert not db.in_transaction()
        for table in (Walk, WalkPet, WalkPointChunk, ActivitySessionLink):
            assert await db.scalar(select(func.count()).select_from(table)) == 0


@pytest.mark.parametrize("delete_winner", [False, True])
async def test_conflict_recovery_holds_game_lock_and_does_not_invent_a_deleted_winner(
    database, actors, monkeypatch, delete_winner
):
    (owner, _), (pet, _, _) = actors
    request = body(uuid.uuid4(), [pet])
    monkeypatch.setattr(settings, "activity_game_enabled", False)
    async with database() as db:
        winner, _ = await service.upload_walk(db, owner, request)
        winner_id = winner.id

    monkeypatch.setattr(settings, "activity_game_enabled", True)
    original_lookup = repo.get_by_client_session
    lookups = 0

    async def stale_first_lookup(*args):
        nonlocal lookups
        lookups += 1
        # Simulate a preflight read preceding a writer that did not take the game lock.
        # The subsequent unique violation, rollback and recovery use real PostgreSQL.
        if lookups == 1:
            return None
        return await original_lookup(*args)

    monkeypatch.setattr(repo, "get_by_client_session", stale_first_lookup)
    original_record = activity.record_walk
    recorded = []

    async def record_with_lock_check(db, walk, *args):
        async with database() as contender:
            # A different connection must not take the game lock during recovery writes.
            assert not await contender.scalar(text("SELECT pg_try_advisory_xact_lock(260,36)"))
        recorded.append(walk.id)
        return await original_record(db, walk, *args)

    monkeypatch.setattr(activity, "record_walk", record_with_lock_check)
    async with database() as db:
        if delete_winner:
            original_rollback = db.rollback

            async def rollback_and_delete():
                await original_rollback()
                async with database() as deleting:
                    await deleting.execute(delete(Walk).where(Walk.id == winner_id))
                    await deleting.commit()

            monkeypatch.setattr(db, "rollback", rollback_and_delete)
            with pytest.raises(IntegrityError) as error:
                await service.upload_walk(db, owner, request)
            assert error.value.orig.__cause__.constraint_name == "walks_client_session_unique"
            assert recorded == []
        else:
            recovered, created = await service.upload_walk(db, owner, request)
            assert recovered.id == winner_id and not created
            assert recorded == [winner_id]
            assert not db.in_transaction()

    assert lookups == 2
    async with database() as db:
        assert await db.scalar(select(func.count()).select_from(Walk)) == int(not delete_winner)
        links = list(await db.scalars(select(ActivitySessionLink)))
        if delete_winner:
            assert links == []
        else:
            assert len(links) == 1 and links[0].walk_id == winner_id
        assert await db.scalar(text("SELECT pg_try_advisory_xact_lock(260,36)"))
