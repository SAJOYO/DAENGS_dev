"""Actual SQL, photo decisions and reward writes in localhost/claims_test only."""

import asyncio
from dataclasses import asdict

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from daengs_backend.models import AppUser, Pet
from daengs_backend.models.activity import ActivityAccount, ActivityGameReceipt, ActivitySeason
from daengs_backend.models.activity_reward import ActivityBaseReward, ActivityRewardDetail
from daengs_backend.services import activity, activity_game, territory_owner
from daengs_backend.services.activity_core import first_season_policy as first
from daengs_backend.services.activity_core.game_policy import HOUR_MS, POINT_DENOMINATOR
from tests.activity.support.actions import mark
from tests.activity.support.database import database as activity_database  # noqa: F401
from tests.territory.certification.test_territory_certified_db import admit, shoot
from tests.territory.support import ownership as base
from tests.territory.support.paths import REPO


@pytest.fixture
async def database(activity_database):  # noqa: F811 - pytest fixture dependency
    async with activity_database() as db:
        raw = (await (await db.connection()).get_raw_connection()).driver_connection
        migration = (REPO / "db/migrations/2026-09-10_activity_rewards.sql").read_text("utf-8")
        await raw.execute(migration)
        await raw.execute(migration)
        await raw.execute((REPO / "db/init/29_activity_rewards.sql").read_text("utf-8"))
        await raw.execute(
            (REPO / "db/migrations/verify_2026-09-10_activity_rewards.sql").read_text("utf-8")
        )
        await db.commit()
    return activity_database


async def start(database, clock, name="first"):
    async with database() as db:
        return await activity_game.create_season(
            db, name, clock[0] - 1000, clock[0] + 24 * HOUR_MS, first.Rules()
        )


async def actor(database, clock, member, pet):
    session = await base.begin(database, member, [pet])
    claim = await mark(database, clock, member, session, pet)
    return member, session, claim


async def certify(database, clock, item, monkeypatch):
    photo = await shoot(database, clock, item)
    await base.decide(database, photo, monkeypatch)
    return photo


async def test_upgrade_holding_worker_and_public_summary(database, actors, clock, monkeypatch):
    (a, _), (pa, _, _) = actors
    await start(database, clock)
    item = await actor(database, clock, a, pa)
    async with database() as db:
        score = (await db.get(ActivityAccount, ("first", pa))).score
        assert score["base_bonus"] == score["bonus"] == 20
    clock[0] += HOUR_MS
    await certify(database, clock, item, monkeypatch)
    clock[0] += HOUR_MS
    async with database() as db:
        await activity.process_pending(db)
    async with database() as db:
        account = await db.get(ActivityAccount, ("first", pa))
        assert account.score["bonus"] == account.score["base_bonus"] == 100
        assert account.score["takeover_bonus"] == 0
        assert account.score["holding_units"] == 12 * POINT_DENOMINATOR
        summary = await territory_owner.summary(db, a, base.SITE)
        assert summary["owner"]["season_record"]["points"] == "112"
        ledger = await db.get(ActivityBaseReward, ("first", a, base.SITE))
        assert ledger.paid == 100
        details = list(
            await db.scalars(
                select(ActivityRewardDetail).order_by(ActivityRewardDetail.base_before)
            )
        )
        assert [(d.base_points, d.takeover_points) for d in details] == [(20, 0), (80, 0)]
    with pytest.raises(base.svc.ClaimConflict, match="already_certified"):
        await admit(database, a, item[2])  # 72h renewal is a subsequent implementation.


@pytest.mark.parametrize("old_verified", [False, True])
async def test_takeover_120_100_20_and_duplicate_photo(
    database, actors, clock, monkeypatch, old_verified
):
    (a, b), (pa, _, pb) = actors
    await start(database, clock)
    ai = await actor(database, clock, a, pa)
    bi = await actor(database, clock, b, pb)
    if old_verified:
        await certify(database, clock, ai, monkeypatch)
        clock[0] += 600_000
    photo = await certify(database, clock, bi, monkeypatch)
    await asyncio.gather(
        base.decide(database, photo, monkeypatch), base.decide(database, photo, monkeypatch)
    )
    async with database() as db:
        result = await db.get(ActivityGameReceipt, ("first", "photo:" + str(photo)))
        assert result.bonus == 120
        assert (await db.get(ActivityAccount, ("first", pb))).score["bonus"] == 120
    clock[0] += 599_999
    with pytest.raises(base.svc.ClaimConflict, match="protected"):
        await admit(database, a, ai[2])
    clock[0] += 1
    retake = await certify(database, clock, ai, monkeypatch)
    async with database() as db:
        result = await db.get(ActivityGameReceipt, ("first", "photo:" + str(retake)))
        assert result.bonus == (20 if old_verified else 100)
    clock[0] += 600_000
    repeat = await certify(database, clock, bi, monkeypatch)
    async with database() as db:
        assert (await db.get(ActivityGameReceipt, ("first", "photo:" + str(repeat)))).bonus == 20
        score = (await db.get(ActivityAccount, ("first", pb))).score
        assert (score["base_bonus"], score["takeover_bonus"], score["bonus"]) == (100, 40, 140)


async def test_two_dogs_share_member_cap_even_concurrently(database, actors, clock, monkeypatch):
    (a, _), (pa, pa2, _) = actors
    await start(database, clock)
    first_dog, second_dog = await asyncio.gather(
        actor(database, clock, a, pa), actor(database, clock, a, pa2)
    )
    granted, challenger = (
        (first_dog, second_dog)
        if first_dog[2].disposition == "GRANTED"
        else (second_dog, first_dog)
    )
    await certify(database, clock, challenger, monkeypatch)
    async with database() as db:
        left = (await db.get(ActivityAccount, ("first", granted[2].claiming_pet_id))).score
        right = (await db.get(ActivityAccount, ("first", challenger[2].claiming_pet_id))).score
        assert left["bonus"] == 20 and right["bonus"] == 80
        assert right["takeovers"] == right["takeover_bonus"] == 0
        assert await db.scalar(select(func.count()).select_from(ActivityBaseReward)) == 1


async def test_reward_storage_failure_rolls_back_photo_ownership_and_scores(
    database,
    actors,
    clock,
    monkeypatch,
):
    (a, b), (pa, _, pb) = actors
    await start(database, clock)
    await actor(database, clock, a, pa)
    bi = await actor(database, clock, b, pb)
    photo = await shoot(database, clock, bi)
    original = Session.flush

    def fail_detail(self, *args, **kwargs):
        if any(isinstance(row, ActivityRewardDetail) for row in self.new):
            raise RuntimeError("reward detail storage failed")
        return original(self, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(Session, "flush", fail_detail)
        with pytest.raises(RuntimeError, match="reward detail storage failed"):
            await base.decide(database, photo, monkeypatch)
    async with database() as db:
        state = (await base.svc.list_sites(db, a, [base.SITE]))[0]
        assert state.occupancy.owner_pet_id == pa
        assert await db.get(ActivityAccount, ("first", pb)) is None
        assert await db.get(ActivityBaseReward, ("first", b, base.SITE)) is None
        assert await db.get(ActivityGameReceipt, ("first", "photo:" + str(photo))) is None
    await base.decide(database, photo, monkeypatch)
    async with database() as db:
        assert (await db.get(ActivityAccount, ("first", pb))).score["bonus"] == 120


async def test_pet_deletion_keeps_member_eligibility_and_withdrawal_cleans_it(
    database,
    actors,
    clock,
):
    (a, _), (pa, pa2, _) = actors
    await start(database, clock)
    await actor(database, clock, a, pa)
    async with database() as db:
        await db.execute(delete(Pet).where(Pet.id == pa))
        await db.commit()
    await actor(database, clock, a, pa2)
    async with database() as db:
        assert (await db.get(ActivityBaseReward, ("first", a, base.SITE))).paid == 20
        assert (await db.get(ActivityAccount, ("first", pa2))).score["bonus"] == 0
        # Real withdrawal removes pets before marking the retained member withdrawn.
        await db.execute(delete(Pet).where(Pet.app_user_id == a))
        await db.execute(update(AppUser).where(AppUser.id == a).values(status="withdrawn"))
        await db.commit()
    async with database() as db:
        assert await db.scalar(select(func.count()).select_from(ActivityBaseReward)) == 0


async def test_finalization_preserves_breakdown_and_new_season_renews_eligibility(
    database,
    actors,
    clock,
):
    (a, _), (pa, _, _) = actors
    season = await start(database, clock)
    await actor(database, clock, a, pa)
    clock[0] = season.ends_ms
    async with database() as db:
        await activity.process_pending(db)
    async with database() as db:
        closed = await db.get(ActivitySeason, "first")
        assert closed.status == "FINALIZED" and closed.rules == asdict(first.Rules())
        account = await db.get(ActivityAccount, ("first", pa))
        assert account.final_score["base_bonus"] == 20
        assert account.final_score["holding_units"] == 48 * POINT_DENOMINATOR
        assert account.score["current_count"] == account.score["scoring_count"] == 0
    clock[0] += 1000
    await start(database, clock, "next")
    await actor(database, clock, a, pa)
    async with database() as db:
        assert (await db.get(ActivityBaseReward, ("first", a, base.SITE))).paid == 20
        assert (await db.get(ActivityBaseReward, ("next", a, base.SITE))).paid == 20
