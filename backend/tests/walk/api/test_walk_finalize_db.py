"""Finalize's external I/O and transaction boundaries in disposable PostgreSQL."""

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from sqlalchemy import delete, func, select, text, update

from daengs_backend.config import settings
from daengs_backend.models import Walk, WalkAnalysis, WalkPet, WalkPointChunk
from daengs_backend.models.activity import ActivityWalkHead
from daengs_backend.models.walk import WalkCapsule, WalkCellophaneSheet
from daengs_backend.orchestration.adapters.life import WalkWeatherObservation
from daengs_backend.schemas.walk import WalkFinalizeRequest, WalkPointsAppend
from daengs_backend.services.walk_session import lifecycle as service
from daengs_backend.services.walk_session.chunk import encode_chunk
from daengs_backend.services.walk_session.finalize import FinalizeInputError
from tests.walk.api.test_walk_upload_db import body


def manifest(count=2):
    return WalkFinalizeRequest(
        expected_point_count=count, terminal_client_seq=count - 1 if count else None
    )


async def upload(database, owner, pets):
    request = body(uuid.uuid4(), pets)
    async with database() as db:
        walk, _ = await service.upload_walk(db, owner, request)
        return walk.id, request


class PausedWeather:
    def __init__(self):
        self.entered = asyncio.Event()
        self.resume = asyncio.Event()
        self.calls = 0
        self.in_transaction = None

    async def __call__(self, *args):
        self.calls += 1
        self.in_transaction = self.db.in_transaction()
        self.entered.set()
        await asyncio.wait_for(self.resume.wait(), timeout=15)
        return WalkWeatherObservation(status="captured", temperature_c=29, provider="test_weather")


@asynccontextmanager
async def pending_finalize(database, owner, walk_id, request=None):
    weather = PausedWeather()

    async def run():
        async with database() as db:
            weather.db = db
            return await service.finalize_walk(db, owner, walk_id, request or manifest(), weather)

    task = asyncio.create_task(run())
    try:
        await asyncio.wait_for(weather.entered.wait(), timeout=5)
        yield task, weather
    finally:
        weather.resume.set()
        await asyncio.gather(task, return_exceptions=True)


async def assert_graph(database, walk_id, *, sealed, game_enabled):
    async with database() as db:
        assert await db.scalar(select(Walk.analysis_state).where(Walk.id == walk_id)) == (
            "derived" if sealed else "collecting"
        )
        for model in (WalkAnalysis, WalkCellophaneSheet, WalkCapsule):
            assert await db.scalar(select(func.count()).select_from(model)) == int(sealed)
        assert await db.scalar(select(func.count()).select_from(ActivityWalkHead)) == int(
            sealed and game_enabled
        )


@pytest.mark.parametrize("game_enabled", [False, True])
async def test_weather_wait_releases_transaction_walk_lock_and_game_lock(
    database, actors, monkeypatch, game_enabled
):
    (owner, other), (pet, _, other_pet) = actors
    monkeypatch.setattr(settings, "activity_game_enabled", game_enabled)
    walk_id, _ = await upload(database, owner, [pet])

    async with pending_finalize(database, owner, walk_id) as (task, weather):
        assert weather.in_transaction is False
        async with database() as contender:
            assert await contender.scalar(text("SELECT pg_try_advisory_xact_lock(260,36)"))
            assert (
                await contender.scalar(
                    text("SELECT id FROM walks WHERE id=:id FOR UPDATE NOWAIT"), {"id": walk_id}
                )
                == walk_id
            )
        # A real competing game-enabled producer must finish before weather is released.
        await asyncio.wait_for(upload(database, other, [other_pet]), timeout=3)
        assert not task.done()

    analysis, created = await task
    assert created and analysis.walk_id == walk_id
    assert analysis.capsule.trail_context["temperature_c"] == 29
    await assert_graph(database, walk_id, sealed=True, game_enabled=game_enabled)


@pytest.mark.parametrize("game_enabled", [False, True])
async def test_concurrent_winner_is_reused_and_late_weather_does_not_replace_its_capsule(
    database, actors, monkeypatch, game_enabled
):
    (owner, _), (pet, _, _) = actors
    monkeypatch.setattr(settings, "activity_game_enabled", game_enabled)
    walk_id, _ = await upload(database, owner, [pet])

    async with pending_finalize(database, owner, walk_id) as (slow, _):
        async with database() as db:
            winner, created = await asyncio.wait_for(
                service.finalize_walk(db, owner, walk_id, manifest()), timeout=3
            )
            winner_context = dict(winner.capsule.trail_context)
            assert created
        assert not slow.done()

    reused, created = await slow
    assert not created and reused.id == winner.id
    assert reused.capsule.trail_context == winner_context
    assert reused.capsule.trail_context["temperature_c"] != 29
    await assert_graph(database, walk_id, sealed=True, game_enabled=game_enabled)


async def test_two_pending_finalizers_publish_one_analysis_and_retry_does_not_call_weather(
    database, actors, monkeypatch
):
    (owner, _), (pet, _, _) = actors
    monkeypatch.setattr(settings, "activity_game_enabled", True)
    walk_id, _ = await upload(database, owner, [pet])

    async with (
        pending_finalize(database, owner, walk_id) as (first, weather1),
        pending_finalize(database, owner, walk_id) as (second, weather2),
    ):
        weather1.resume.set()
        weather2.resume.set()
        results = await asyncio.wait_for(asyncio.gather(first, second), timeout=5)
    assert results[0][0].id == results[1][0].id
    assert sorted(created for _, created in results) == [False, True]
    assert weather1.calls == weather2.calls == 1

    calls = []

    async def unexpected_weather(*args):
        calls.append(args)

    async with database() as db:
        repeated, created = await service.finalize_walk(
            db, owner, walk_id, manifest(), unexpected_weather
        )
        assert not created and repeated.id == results[0][0].id
    assert calls == []
    await assert_graph(database, walk_id, sealed=True, game_enabled=True)


@pytest.mark.parametrize("change", ["coordinates", "time", "weather", "pets", "recording"])
async def test_recheck_refreshes_existing_orm_rows_and_all_calculation_inputs(
    database, actors, monkeypatch, change
):
    (owner, _), (pet1, pet2, _) = actors
    monkeypatch.setattr(settings, "activity_game_enabled", True)
    walk_id, request = await upload(database, owner, [pet1, pet2])

    async with pending_finalize(database, owner, walk_id) as (task, _), database() as changing:
        if change in {"coordinates", "recording"}:
            if change == "coordinates":
                request.points[0].lng += 1
            else:
                # Recording eligibility is a separate receipt, outside analysis v1 identity.
                request.points[0].recording_eligible = False
            await changing.execute(
                update(WalkPointChunk)
                .where(WalkPointChunk.walk_id == walk_id)
                .values(payload=encode_chunk(request.points))
            )
        elif change == "time":
            await changing.execute(
                update(Walk)
                .where(Walk.id == walk_id)
                .values(ended_at=request.ended_at + timedelta(seconds=10))
            )
        elif change == "weather":
            await changing.execute(update(Walk).where(Walk.id == walk_id).values(weather_code=62))
        else:
            await changing.execute(
                delete(WalkPet).where(WalkPet.walk_id == walk_id, WalkPet.pet_id == pet2)
            )
        await asyncio.wait_for(changing.commit(), timeout=3)

    if change == "recording":
        assert (await task)[1] is True
    else:
        with pytest.raises(service.WalkStateConflictError) as error:
            await task
        assert error.value.code == "walk_input_changed"
        await assert_graph(database, walk_id, sealed=False, game_enabled=True)
        async with database() as db:
            assert (await service.finalize_walk(db, owner, walk_id, manifest()))[1] is True
    await assert_graph(database, walk_id, sealed=True, game_enabled=True)


@pytest.mark.parametrize("change", ["delete", "owner"])
async def test_deleted_or_reassigned_walk_is_not_sealed_after_weather(
    database, actors, monkeypatch, change
):
    (owner, other), (pet, _, _) = actors
    monkeypatch.setattr(settings, "activity_game_enabled", True)
    walk_id, _ = await upload(database, owner, [pet])
    async with pending_finalize(database, owner, walk_id) as (task, _), database() as changing:
        statement = (
            delete(Walk).where(Walk.id == walk_id)
            if change == "delete"
            else update(Walk).where(Walk.id == walk_id).values(app_user_id=other)
        )
        await changing.execute(statement)
        await asyncio.wait_for(changing.commit(), timeout=3)
    with pytest.raises(service.WalkNotFoundError):
        await task
    async with database() as db:
        for model in (WalkAnalysis, WalkCellophaneSheet, WalkCapsule, ActivityWalkHead):
            assert await db.scalar(select(func.count()).select_from(model)) == 0


async def test_final_write_failure_rolls_back_analysis_capsule_state_and_activity(
    database, actors, monkeypatch
):
    (owner, _), (pet, _, _) = actors
    monkeypatch.setattr(settings, "activity_game_enabled", True)
    walk_id, _ = await upload(database, owner, [pet])
    async with database() as db:
        original_commit = db.commit
        commits = 0

        async def fail_final_commit():
            nonlocal commits
            commits += 1
            if commits == 2:
                raise RuntimeError("injected final commit failure")
            await original_commit()

        monkeypatch.setattr(db, "commit", fail_final_commit)
        with pytest.raises(RuntimeError, match="injected final commit failure"):
            await service.finalize_walk(db, owner, walk_id, manifest())
        assert commits == 2 and not db.in_transaction()
    await assert_graph(database, walk_id, sealed=False, game_enabled=True)
    async with database() as db:
        assert (await service.finalize_walk(db, owner, walk_id, manifest()))[1] is True
    await assert_graph(database, walk_id, sealed=True, game_enabled=True)


async def test_cancel_during_weather_leaves_walk_collecting_and_retryable(
    database, actors, monkeypatch
):
    (owner, _), (pet, _, _) = actors
    monkeypatch.setattr(settings, "activity_game_enabled", True)
    walk_id, _ = await upload(database, owner, [pet])
    async with pending_finalize(database, owner, walk_id) as (task, _):
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    await assert_graph(database, walk_id, sealed=False, game_enabled=True)
    async with database() as db:
        assert (await service.finalize_walk(db, owner, walk_id, manifest()))[1] is True


async def test_legacy_capsule_repair_refreshes_same_session_and_preserves_analysis_id(
    database, actors, monkeypatch
):
    (owner, _), (pet, _, _) = actors
    monkeypatch.setattr(settings, "activity_game_enabled", True)
    walk_id, _ = await upload(database, owner, [pet])
    calls = []

    async def weather(*args):
        calls.append(args)

    async with database() as db:
        original, _ = await service.finalize_walk(db, owner, walk_id, manifest(), weather)
        original_capsule = original.capsule
        async with database() as deleting:
            await deleting.execute(
                delete(WalkCapsule).where(WalkCapsule.analysis_id == original.id)
            )
            await deleting.commit()
        repaired, created = await service.finalize_walk(db, owner, walk_id, manifest(), weather)
        assert not created and repaired.id == original.id
        assert repaired.capsule is not original_capsule
        assert repaired.capsule.sealed_at == original.derived_at
        assert repaired.capsule.trail_context["provider"] == "legacy_walk_metadata_v1"
        assert len(calls) == 1
    await assert_graph(database, walk_id, sealed=True, game_enabled=True)


@pytest.mark.parametrize("game_enabled", [False, True])
async def test_append_during_weather_is_seen_and_stale_manifest_does_not_seal(
    database, actors, monkeypatch, game_enabled
):
    (owner, _), (pet, _, _) = actors
    monkeypatch.setattr(settings, "activity_game_enabled", game_enabled)
    walk_id, request = await upload(database, owner, [pet])
    point = request.points[0].model_copy(update={"client_seq": 2})

    async with pending_finalize(database, owner, walk_id) as (task, _):
        async with database() as db:
            appended = await asyncio.wait_for(
                service.append_points(db, owner, walk_id, WalkPointsAppend(points=[point])),
                timeout=3,
            )
            assert len(appended.points) == 2
        assert not task.done()

    with pytest.raises(FinalizeInputError) as error:
        await task
    assert error.value.code == "point_count_mismatch"
    await assert_graph(database, walk_id, sealed=False, game_enabled=game_enabled)
    async with database() as db:
        analysis, created = await service.finalize_walk(db, owner, walk_id, manifest(3))
        assert created and analysis.point_count == 3
    await assert_graph(database, walk_id, sealed=True, game_enabled=game_enabled)
