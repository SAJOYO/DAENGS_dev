"""Monthly seasons on disposable PostgreSQL: boundaries, rollback, replay and photo isolation."""

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from unittest.mock import Mock

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from daengs_backend.models.activity import ActivityAccount, ActivityMonthlySeason, ActivitySeason
from daengs_backend.models.activity_reward import ActivityBaseReward
from daengs_backend.models.territory_claim import TerritoryOccupancy
from daengs_backend.schemas.activity import TerritorySummaryResponse
from daengs_backend.services import activity, activity_game
from daengs_backend.services.activity_core import first_season_policy as first
from daengs_backend.services.activity_core.game_policy import POINT_DENOMINATOR
from daengs_backend.services.activity_core.monthly_calendar import month
from tests.activity.support.actions import begin, mark
from tests.activity.support.database import database as activity_database  # noqa: F401
from tests.activity.test_first_season_db import actor, certify
from tests.activity.test_first_season_db import database as reward_database  # noqa: F401
from tests.activity.test_monthly_calendar import stamp
from tests.territory.certification.test_territory_certified_db import result, shoot
from tests.territory.support import ownership as base
from tests.territory.support.paths import REPO


@pytest.fixture
async def database(reward_database):  # noqa: F811
    async with reward_database() as db:
        raw = (await (await db.connection()).get_raw_connection()).driver_connection
        for name in (
            "db/migrations/2026-09-10_activity_monthly.sql",
            "db/init/31_activity_monthly.sql",
            "db/migrations/verify_2026-09-10_activity_monthly.sql",
        ):
            await raw.execute((REPO / name).read_text("utf-8"))
        await db.commit()
    return reward_database


async def start(database, clock, when="2026-09-30T22:00:00+09:00"):
    clock[0] = stamp(when)
    key, _, end = month(clock[0])
    async with database() as db:
        return await activity_game.create_season(
            db, key, clock[0], end, first.Rules(), monthly=True
        )


async def process(database):
    async with database() as db:
        return await activity.process_pending(db)


async def test_partial_first_season_and_exact_midnight(database, actors, clock):
    (a, b), (pa, _, pb) = actors
    season = await start(database, clock)
    assert season.coverage_start_ms == season.starts_ms == clock[0]
    await actor(database, clock, a, pa)
    client = await begin(database, clock, b, [pb])
    await mark(database, clock, b, client, pb, site_id=base.SITE2)
    clock[0] = season.ends_ms - 1
    await process(database)
    async with database() as db:
        assert (await activity_game.repo.active_season(db)).id == season.id
    clock[0] += 1
    await asyncio.gather(process(database), process(database))
    async with database() as db:
        closed = await db.get(ActivitySeason, season.id)
        assert closed.status == "FINALIZED" and closed.confirmed_ms == season.ends_ms
        current = await activity_game.repo.active_season(db)
        assert current.id == "territory-2026-10" and current.starts_ms == season.ends_ms
        assert current.ends_ms == stamp("2026-11-01T00:00:00+09:00")
        assert current.rules == asdict(first.Rules())
        assert (await db.get(ActivityMonthlySeason, current.id)).previous_season_id == season.id
        assert await db.scalar(select(func.count()).select_from(TerritoryOccupancy)) == 0
        for member, pet in [(a, pa), (b, pb)]:
            row = await db.get(ActivityAccount, (season.id, pet))
            assert row.final_rank == 1  # Equal exact scores share rank.
            assert row.final_score["holding_units"] == 4 * POINT_DENOMINATOR
            assert row.final_score["bonus"] == 20
            assert (
                await db.get(
                    ActivityBaseReward, (season.id, member, base.SITE if pet == pa else base.SITE2)
                )
            ).paid == 20
            summary = TerritorySummaryResponse.model_validate(
                await activity.territory_summary(db, member, season.id, pet)
            )
            assert summary.final_rank == 1
        original = (await db.get(ActivityAccount, (season.id, pa))).final_score.copy()
    await actor(database, clock, a, pa)
    await process(database)
    async with database() as db:
        assert (await db.get(ActivityBaseReward, ("territory-2026-10", a, base.SITE))).paid == 20
        assert (await db.get(ActivityAccount, (season.id, pa))).final_score == original
        assert await db.scalar(select(func.count()).select_from(ActivitySeason)) == 2


@pytest.mark.parametrize(
    "wall_now",
    ["2026-01-01T00:00:00+00:00", "2026-09-12T00:00:00+00:00", "2030-01-01T00:00:00+00:00"],
    ids=["before_scenario", "after_scenario", "years_later"],
)
async def test_downtime_catches_up_months_without_carrying_holdings(
    database, actors, clock, monkeypatch, wall_now
):
    # Vary only the builder's wall clock; game time and the database clock stay separate.
    wall_datetime = Mock(wraps=datetime)
    wall_datetime.now.return_value = datetime.fromisoformat(wall_now)
    monkeypatch.setattr(base, "datetime", wall_datetime)
    (a, _), (pa, _, _) = actors
    season = await start(database, clock, "2026-09-11T12:00:00+09:00")
    _, client, _ = await actor(database, clock, a, pa)
    async with database() as db:
        session = await base.svc.get_session(db, a, client)
        assert session.started_at == datetime.fromtimestamp(clock[0] / 1000, UTC) - timedelta(
            minutes=1
        )
    clock[0] = stamp("2027-01-04T10:00:00+09:00")
    await process(database)
    async with database() as db:
        seasons = list(await db.scalars(select(ActivitySeason).order_by(ActivitySeason.starts_ms)))
        assert len(seasons) == 5
        assert [s.status for s in seasons] == ["FINALIZED"] * 4 + ["ACTIVE"]
        assert seasons[-1].id == "territory-2027-01"
        for left, right in pairwise(seasons):
            assert left.ends_ms == right.starts_ms
        row = await db.get(ActivityAccount, (season.id, pa))
        assert row.final_score["holding_units"] == 144 * POINT_DENOMINATOR
        assert row.final_rank == 1
        assert await db.scalar(select(func.count()).select_from(ActivityAccount)) == 1
    await process(database)
    async with database() as db:
        assert await db.scalar(select(func.count()).select_from(ActivitySeason)) == 5


async def test_successor_failure_rolls_back_finalization_and_can_retry(
    database, actors, clock, monkeypatch
):
    (a, _), (pa, _, _) = actors
    season = await start(database, clock)
    await actor(database, clock, a, pa)
    clock[0] = season.ends_ms
    original = Session.flush

    def fail(self, *args, **kwargs):
        if any(
            isinstance(row, ActivityMonthlySeason) and row.previous_season_id for row in self.new
        ):
            raise RuntimeError("successor storage failed")
        return original(self, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(Session, "flush", fail)
        with pytest.raises(RuntimeError, match="successor storage failed"):
            await process(database)
    async with database() as db:
        assert (await db.get(ActivitySeason, season.id)).status == "ACTIVE"
        row = await db.get(ActivityAccount, (season.id, pa))
        assert row.final_score is None and row.final_rank is None
        assert await db.get(TerritoryOccupancy, base.SITE) is not None
        assert await db.get(ActivitySeason, "territory-2026-10") is None
    await process(database)
    async with database() as db:
        assert (await activity_game.repo.active_season(db)).id == "territory-2026-10"


async def test_late_photo_cannot_score_new_season_and_fresh_photo_can(
    database, actors, clock, monkeypatch
):
    (a, _), (pa, _, _) = actors
    season = await start(database, clock)
    item = await actor(database, clock, a, pa)
    clock[0] = season.ends_ms - 1
    photo = await shoot(database, clock, item)
    clock[0] += 1
    # Photo callback itself must advance seasons even when Beat has not run yet.
    await base.decide(database, photo, monkeypatch)
    current = await result(database, item)
    assert current.resolution_code == "season_ended" and current.photo_status == "VERIFIED"
    assert current.site.season_id == "territory-2026-10" and current.site.occupancy is None
    async with database() as db:
        assert await db.get(ActivityBaseReward, ("territory-2026-10", a, base.SITE)) is None
    await certify(database, clock, item, monkeypatch)
    async with database() as db:
        assert (await db.get(ActivityBaseReward, ("territory-2026-10", a, base.SITE))).paid == 100
        assert (await db.get(ActivityAccount, ("territory-2026-10", pa))).score[
            "takeover_bonus"
        ] == 0


async def test_worker_never_auto_starts_first_or_manual_season(database, clock):
    await process(database)
    async with database() as db:
        assert await activity_game.repo.active_season(db) is None
    async with database() as db:
        await activity_game.create_season(db, "manual", clock[0], clock[0] + 1000, first.Rules())
    clock[0] += 1000
    await process(database)
    async with database() as db:
        assert await activity_game.repo.active_season(db) is None
        assert await db.scalar(select(func.count()).select_from(ActivityMonthlySeason)) == 0


async def test_first_activation_rejects_bad_calendar_and_duplicate_start(database, clock):
    clock[0] = stamp("2026-09-11T12:00:00+09:00")
    key, _, end = month(clock[0])
    async with database() as db:
        with pytest.raises(ValueError, match="monthly_boundary_mismatch"):
            await activity_game.create_season(
                db, key, clock[0], end + 1000, first.Rules(), monthly=True
            )
    await start(database, clock, "2026-09-11T12:00:00+09:00")
    async with database() as db:
        with pytest.raises(ValueError, match="active_season_exists"):
            await activity_game.create_season(db, key, clock[0], end, first.Rules(), monthly=True)
    async with database() as db:
        assert await db.scalar(select(func.count()).select_from(ActivitySeason)) == 1


async def test_conflicting_successor_does_not_overwrite_history(database, actors, clock):
    (a, _), (pa, _, _) = actors
    season = await start(database, clock)
    await actor(database, clock, a, pa)
    key, start_ms, end_ms = month(season.ends_ms)
    async with database() as db:
        # A preexisting inconsistent calendar row must fail closed, not be silently adopted.
        db.add(
            ActivitySeason(
                id=key,
                starts_ms=start_ms,
                ends_ms=end_ms,
                coverage_start_ms=start_ms,
                confirmed_ms=end_ms,
                revision=1,
                status="FINALIZED",
                rules=asdict(first.Rules()),
            )
        )
        await db.commit()
    clock[0] = season.ends_ms
    with pytest.raises(ValueError, match="monthly_season_conflict"):
        await process(database)
    async with database() as db:
        assert (await db.get(ActivitySeason, season.id)).status == "ACTIVE"
        assert (await db.get(ActivitySeason, key)).status == "FINALIZED"
        assert (await db.get(ActivityAccount, (season.id, pa))).final_score is None
        assert await db.get(TerritoryOccupancy, base.SITE) is not None
