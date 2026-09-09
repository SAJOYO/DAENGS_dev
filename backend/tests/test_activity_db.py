"""Production SQL and complete producer/worker/read paths in a disposable PostgreSQL schema."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import test_territory_ownership_db as base
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from daengs_backend.config import settings
from daengs_backend.core.database import get_session
from daengs_backend.core.deps import AppPrincipal, current_app_user
from daengs_backend.main import app
from daengs_backend.models import AppUser, Pet, TerritoryAttempt
from daengs_backend.models.activity import (
    ActivityAccount,
    ActivityGameReceipt,
    ActivitySeason,
    ActivitySessionLink,
    ActivityWalkHead,
)
from daengs_backend.models.territory_claim import TerritoryClaimSession, TerritoryOccupancy
from daengs_backend.repositories import activity as repo
from daengs_backend.repositories import walk as walk_repo
from daengs_backend.schemas.walk import WalkFinalizeRequest, WalkPointUpload, WalkUpload
from daengs_backend.services import activity, activity_game
from daengs_backend.services import walk as walks
from daengs_backend.services.activity_core import game_policy as policy

territory_database = base.database
actors = base.actors


@pytest.fixture
async def database(territory_database):
    async with territory_database() as db:
        raw = (await (await db.connection()).get_raw_connection()).driver_connection
        await raw.execute((base.ROOT / "db/init/06_walks.sql").read_text(encoding="utf-8"))
        sql = (base.ROOT / "db/migrations/2026-09-06_activity_game.sql").read_text(encoding="utf-8")
        await raw.execute(sql)
        await raw.execute(sql)
        await raw.execute((base.ROOT / "db/init/21_activity_game.sql").read_text(encoding="utf-8"))
        await raw.execute(
            (base.ROOT / "db/migrations/verify_2026-09-06_activity_game.sql").read_text(
                encoding="utf-8"
            )
        )
        await db.commit()
    return territory_database


@pytest.fixture
def clock(monkeypatch):
    current = [int(datetime.now(UTC).timestamp() * 1000)]
    monkeypatch.setattr(settings, "activity_game_enabled", True)
    monkeypatch.setattr(activity_game, "now_ms", lambda: current[0])
    monkeypatch.setattr(base.svc, "_now", lambda: datetime.fromtimestamp(current[0] / 1000, UTC))
    return current


async def season(database, clock, name="test", **rule_values):
    # Preserve the already-running season contract; v2 scenarios opt in explicitly.
    rule_values.setdefault("version", "draft-2026-09-06")
    async with database() as db:
        return await activity_game.create_season(
            db, name, clock[0] - 1000, clock[0] + 86_400_000, policy.Rules(**rule_values)
        )


async def mark(database, clock, owner, client, pet, **overrides):
    return await base.mark(
        database,
        owner,
        client,
        pet,
        observed_at=datetime.fromtimestamp(clock[0] / 1000, UTC),
        **overrides,
    )


async def certify(database, clock, owner, client, claim, monkeypatch):
    photo = await base.photo(
        database, owner, client, claim, captured_at=datetime.fromtimestamp(clock[0] / 1000, UTC)
    )
    await base.decide(database, photo, monkeypatch)
    return photo


async def upload(database, owner, pets, client, started, *, observed=True):
    points = (
        [
            WalkPointUpload(
                client_seq=i,
                chain_index=0,
                at=started + timedelta(seconds=i * 10),
                lat="37.5",
                lng=str(127 + i * 0.000113),
                accuracy_m=8,
            )
            for i in range(7)
        ]
        if observed
        else []
    )
    body = WalkUpload(
        client_session_id=client,
        pet_ids=pets,
        started_at=started,
        ended_at=started + timedelta(seconds=60),
        points=points,
    )
    async with database() as db:
        row, _ = await walks.upload_walk(db, owner, body)
    manifest = WalkFinalizeRequest(
        expected_point_count=len(points), terminal_client_seq=6 if points else None
    )
    async with database() as db:
        analysis, _ = await walks.finalize_walk(db, owner, row.id, manifest)
    return row, analysis, manifest


async def test_real_walk_sealing_links_pending_worker_restart_rebuild(database, actors, clock):
    (owner, _), (pet, pet2, _) = actors
    client = await base.begin(database, owner, [pet, pet2])
    async with database() as db:
        link = await activity.links(db, owner, client)
        assert link["status"] == "WAITING_FOR_WALK"
        game = await db.get(TerritoryClaimSession, link["game_session_id"])
        started = game.started_at
    walk, analysis, manifest = await upload(database, owner, [pet, pet2], client, started)
    async with database() as db:
        assert (await activity.links(db, owner, client))["status"] == "LINKED"
        pending = await activity.walk_summary(db, owner, 0, clock[0] + 120000)
        assert pending["status"] == "PENDING" and pending["moving_distance_m"] is None
    async with database() as db:
        assert await activity.process_pending(db) == 1
    async with database() as db:
        ready = await activity.walk_summary(db, owner, 0, clock[0] + 120000)
        pet_ready = await activity.walk_summary(db, owner, 0, clock[0] + 120000, pet2)
        assert ready["status"] == "READY" and ready["recorded_walk_count"] == 1
        assert ready["moving_distance_m"] > 0
        assert pet_ready["moving_distance_m"] == ready["moving_distance_m"]
        assert ready["sources"][0]["analysis_id"] == analysis.id
    async with database() as db:
        repeated, created = await walks.finalize_walk(db, owner, walk.id, manifest)
        assert not created and repeated.id == analysis.id
    async with database() as db:
        assert await activity.process_pending(db) == 0
        await activity.rebuild(db)
    async with database() as db:
        assert await activity.process_pending(db) == 1
        assert await activity.walk_summary(db, owner, 0, clock[0] + 120000) == ready


async def test_empty_observation_and_failure_rollback_are_not_zero_measurements(
    database, actors, clock, monkeypatch
):
    (owner, _), (pet, _, _) = actors
    await upload(
        database,
        owner,
        [pet],
        uuid.uuid4(),
        datetime.now(UTC) - timedelta(minutes=2),
        observed=False,
    )
    original = activity.project_walks

    def fail(*args, **kwargs):
        raise RuntimeError("projection crashed")

    monkeypatch.setattr(activity, "project_walks", fail)
    async with database() as db:
        with pytest.raises(RuntimeError, match="crashed"):
            await activity.process_pending(db)
        assert (await db.scalar(select(ActivityWalkHead))).processed_revision == 0
    monkeypatch.setattr(activity, "project_walks", original)
    async with database() as db:
        await activity.process_pending(db)
        value = await activity.walk_summary(db, owner, 0, clock[0])
        assert value["recorded_walk_count"] == 1 and value["observed_walk_count"] == 0
        assert value["moving_s"] is None
        # An empty stream has unknown origin; origin exclusion precedes observability.
        assert value["exclusions"] == (("non_device_evidence", 1),)


async def test_protected_visit_survives_then_takeover_has_one_bonus(
    database, actors, clock, monkeypatch
):
    (a, b), (pa, _, pb) = actors
    await season(database, clock)
    sa = await base.begin(database, a, [pa])
    sb = await base.begin(database, b, [pb])
    first = await mark(database, clock, a, sa, pa)
    original_time = first.site.occupancy.occupied_at
    challenger = await mark(database, clock, b, sb, pb)
    photo = await certify(database, clock, b, sb, challenger, monkeypatch)
    async with database() as db:
        result = await base.svc.get_claim(db, b, challenger.claim_id)
        assert result.resolution_code == "protected" and result.disposition != "GRANTED"
        assert result.site.occupancy.owner_pet_id == pa
        assert (await db.get(TerritoryAttempt, photo)).status == "VERIFIED"
        assert await db.scalar(select(func.count()).select_from(ActivityGameReceipt)) == 1
    clock[0] += 600000
    sb2 = await base.begin(database, b, [pb])
    challenger2 = await mark(database, clock, b, sb2, pb)
    photo2 = await certify(database, clock, b, sb2, challenger2, monkeypatch)
    await base.decide(database, photo2, monkeypatch)
    async with database() as db:
        assert (
            await base.svc.get_claim(db, b, challenger2.claim_id)
        ).site.occupancy.owner_pet_id == pb
        assert (await db.get(ActivityAccount, ("test", pa))).score["held_site_ms"] == 600000
        assert (await db.get(ActivityAccount, ("test", pb))).score["bonus"] == 100
        assert len(await repo.periods(db, "test")) == 2
        assert original_time == datetime.fromtimestamp((clock[0] - 600000) / 1000, UTC)


async def test_concurrent_mark_and_certification_preserve_time_and_peak(
    database, actors, clock, monkeypatch
):
    (owner, _), (pet, _, _) = actors
    await season(database, clock)
    client = await base.begin(database, owner, [pet])
    body = base.mark_body(client, pet, observed_at=datetime.fromtimestamp(clock[0] / 1000, UTC))

    async def send():
        async with database() as db:
            return await base.svc.mark(db, owner, body, base.Lookup())

    values = await asyncio.gather(send(), send(), send())
    assert len({r.claim_id for r in values}) == 1
    await certify(database, clock, owner, client, values[0], monkeypatch)
    clock[0] += 2000
    await mark(database, clock, owner, client, pet, site_id=base.SITE2)
    clock[0] += 3000
    async with database() as db:
        await activity.process_pending(db)
        result = await activity.territory_summary(db, owner, "test", pet)
        assert result["status"] == "READY"
        assert result["statistics"]["acquisition_count"] == 2
        assert result["statistics"]["peak_owned_site_count"] == 2
        assert result["statistics"]["held_site_ms"] == 8000
        assert result["score"]["holding_units"] == 2000 * 10 * 10000 + 3000 * 2 * 10 * 11000
        assert (await db.get(TerritoryOccupancy, base.SITE)).occupied_at == values[
            0
        ].site.occupancy.occupied_at


async def test_season_close_is_replayable_retains_statistics_invalidates_old_photos(
    database, actors, clock, monkeypatch
):
    (a, b), (pa, _, pb) = actors
    original = await season(database, clock)
    sa, sb = await base.begin(database, a, [pa]), await base.begin(database, b, [pb])
    await mark(database, clock, a, sa, pa)
    challenger = await mark(database, clock, b, sb, pb)
    photo = await base.photo(
        database, b, sb, challenger, captured_at=datetime.fromtimestamp(clock[0] / 1000, UTC)
    )
    clock[0] = original.ends_ms + 1
    async with database() as db:
        await activity.process_pending(db)
        final = await activity.territory_summary(db, a, "test", pa)
        assert (await db.get(ActivitySeason, "test")).status == "FINALIZED"
        assert final["statistics"]["owned_site_count"] == 0
        assert final["score"]["held_site_ms"] == 86400000
        assert await db.get(TerritoryOccupancy, base.SITE) is None
    async with database() as db:
        assert await activity.process_pending(db) == 0
        assert await activity.territory_summary(db, a, "test", pa) == final
    await base.decide(database, photo, monkeypatch)
    async with database() as db:
        assert (
            await base.svc.get_claim(db, b, challenger.claim_id)
        ).resolution_code == "site_changed"
    async with database() as db:
        await activity_game.create_season(
            db, "next", original.ends_ms, clock[0] + 100000, policy.Rules()
        )
        assert (await activity.territory_summary(db, a, "next", pa))["statistics"][
            "acquisition_count"
        ] == 0


async def test_import_and_disabled_writer_guard_and_pet_erasure(
    database, actors, clock, monkeypatch
):
    (owner, _), (pet, pet2, _) = actors
    monkeypatch.setattr(settings, "activity_game_enabled", False)
    client = await base.begin(database, owner, [pet, pet2])
    claim = await mark(database, clock, owner, client, pet)
    clock[0] += 100
    monkeypatch.setattr(settings, "activity_game_enabled", True)
    await season(database, clock)
    async with database() as db:
        account = await db.get(ActivityAccount, ("test", pet))
        assert account.score["bonus"] == 0 and account.score["current_count"] == 1
        assert (await repo.periods(db, "test"))[0].origin == "IMPORTED"
    async with database() as db:
        await db.execute(delete(TerritoryOccupancy))
        with pytest.raises(IntegrityError, match="activity ownership source mismatch"):
            await db.commit()
        await db.rollback()
    await certify(database, clock, owner, client, claim, monkeypatch)
    async with database() as db:
        await repo.barrier(db)
        await db.execute(delete(Pet).where(Pet.id == pet))
        await db.commit()
    async with database() as db:
        assert await db.get(ActivityAccount, ("test", pet)) is None
        assert not await repo.periods(db, "test", pet)
        assert pet not in (await db.scalar(select(TerritoryClaimSession))).pet_ids
        assert await db.get(TerritoryOccupancy, base.SITE) is None


async def test_api_owner_scope_validation_pending_and_ready(database, actors, clock):
    (owner, stranger), (pet, _, _) = actors
    await season(database, clock)
    client = await base.begin(database, owner, [pet])
    await mark(database, clock, owner, client, pet)

    async def session():
        async with database() as db:
            yield db

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[current_app_user] = lambda: AppPrincipal(app_user_id=owner)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            path = f"/app/activity/territory/test/pets/{pet}"
            assert (await http.get(path)).json()["status"] == "PENDING"
            async with database() as db:
                await activity.process_pending(db)
            assert (await http.get(path)).json()["status"] == "READY"
            assert (
                await http.get("/app/activity/walks/summary?from_ms=9&to_ms=2")
            ).status_code == 422
            app.dependency_overrides[current_app_user] = lambda: AppPrincipal(app_user_id=stranger)
            assert (await http.get(path)).status_code == 404
            assert (await http.get(f"/app/activity/sessions/{client}")).status_code == 404
    finally:
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(current_app_user, None)


async def test_daily_bonus_is_not_reawarded_after_real_reacquisition(
    database, actors, clock, monkeypatch
):
    (a, b), (pa, _, pb) = actors
    # Keep the three changes within one UTC date even if the test runs near midnight.
    clock[0] -= clock[0] % 86_400_000
    clock[0] += 86_400_000 + 43_200_000
    await season(database, clock, repeat_bonus="daily_pet_site")
    sa = await base.begin(database, a, [pa])
    await mark(database, clock, a, sa, pa)
    for owner, pet in [(b, pb), (a, pa)]:
        clock[0] += 600000
        client = await base.begin(database, owner, [pet])
        claim = await mark(database, clock, owner, client, pet)
        await certify(database, clock, owner, client, claim, monkeypatch)
    async with database() as db:
        account = await db.get(ActivityAccount, ("test", pa))
        assert account.score["claims"] == 2 and account.score["bonus"] == 100
        assert len(await repo.periods(db, "test", pa)) == 2


async def test_simultaneous_photos_only_one_owner_and_small_batches_do_not_starve(
    database, actors, clock, monkeypatch
):
    (a, b), (pa, pa2, pb) = actors
    await season(database, clock)
    sa = await base.begin(database, a, [pa])
    await mark(database, clock, a, sa, pa)
    clock[0] += 600000
    attempts = []
    for owner, pet in [(a, pa2), (b, pb)]:
        client = await base.begin(database, owner, [pet])
        claim = await mark(database, clock, owner, client, pet)
        photo = await base.photo(
            database, owner, client, claim, captured_at=datetime.fromtimestamp(clock[0] / 1000, UTC)
        )
        attempts.append((owner, pet, claim, photo))
    await asyncio.gather(
        *(base.decide(database, photo, monkeypatch) for _, _, _, photo in attempts)
    )
    async with database() as db:
        results = [
            await base.svc.get_claim(db, owner, claim.claim_id) for owner, _, claim, _ in attempts
        ]
        assert sum(result.resolution_code == "site_changed" for result in results) == 1
        assert len(await repo.accounts(db, "test")) == 2
        assert await db.scalar(select(func.count()).select_from(ActivityGameReceipt)) == 2
    for _ in range(2):
        clock[0] += 1000
        async with database() as db:
            await activity.process_pending(db, limit=1)
    async with database() as db:
        assert all(row.processed_revision > 0 for row in await repo.accounts(db, "test"))


async def test_verdict_policy_sources_and_score_roll_back_together(
    database, actors, clock, monkeypatch
):
    (owner, _), (pet, _, _) = actors
    await season(database, clock)
    client = await base.begin(database, owner, [pet])
    claim = await mark(database, clock, owner, client, pet)
    photo = await base.photo(
        database, owner, client, claim, captured_at=datetime.fromtimestamp(clock[0] / 1000, UTC)
    )
    original = base.svc._save_site

    async def fail(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("after policy write")

    monkeypatch.setattr(base.svc, "_save_site", fail)
    with pytest.raises(RuntimeError, match="after policy write"):
        await base.decide(database, photo, monkeypatch)
    async with database() as db:
        attempt = await db.get(TerritoryAttempt, photo)
        assert attempt.status == "VISION_PENDING"
        assert (await db.get(TerritoryOccupancy, base.SITE)).certification == "UNVERIFIED"
        assert (await repo.periods(db, "test"))[0].verified_from_ms is None
        assert await db.scalar(select(func.count()).select_from(ActivityGameReceipt)) == 1
    monkeypatch.setattr(base.svc, "_save_site", original)
    await base.decide(database, photo, monkeypatch)
    async with database() as db:
        assert (await db.get(TerritoryOccupancy, base.SITE)).certification == "VERIFIED"
        assert (await db.get(ActivityAccount, ("test", pet))).score["bonus"] == 100


async def test_shared_walk_survives_pet_delete_and_withdrawal_erases_links_when_disabled(
    database, actors, clock, monkeypatch
):
    (owner, _), (pet, pet2, _) = actors
    client = await base.begin(database, owner, [pet, pet2])
    await upload(database, owner, [pet, pet2], client, datetime.now(UTC) - timedelta(minutes=2))
    async with database() as db:
        await activity.process_pending(db)
        before = await activity.walk_summary(db, owner, 0, clock[0], pet2)
    async with database() as db:
        await repo.barrier(db)
        await walk_repo.delete_walks_only_with(db, pet)
        await db.execute(delete(Pet).where(Pet.id == pet))
        await db.commit()
    async with database() as db:
        after = await activity.walk_summary(db, owner, 0, clock[0], pet2)
        assert after == before
        with pytest.raises(activity.ActivityNotFound):
            await activity.walk_summary(db, owner, 0, clock[0], pet)
    monkeypatch.setattr(settings, "activity_game_enabled", False)
    async with database() as db:
        await walk_repo.delete_all_for_owner(db, owner)
        await db.execute(delete(Pet).where(Pet.app_user_id == owner))
        await db.execute(update(AppUser).where(AppUser.id == owner).values(status="withdrawn"))
        await db.commit()
    async with database() as db:
        assert await db.get(ActivitySessionLink, (owner, client)) is None
        assert not list(
            await db.scalars(
                select(TerritoryClaimSession).where(TerritoryClaimSession.app_user_id == owner)
            )
        )
        assert await db.scalar(select(func.count()).select_from(ActivityWalkHead)) == 0
