"""Current ownership and live keyset pagination against disposable PostgreSQL."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from daengs_backend.models.activity import ActivityHoldingPeriod, ActivitySeason
from daengs_backend.models.territory_claim import TerritoryOccupancy
from daengs_backend.services import activity, activity_game, territory_owned
from daengs_backend.services.territory_site_lookup import TerritorySiteSnapshot
from tests.activity.support.actions import mark
from tests.activity.support.database import database as activity_database  # noqa: F401
from tests.activity.test_first_season_db import actor, certify, start
from tests.activity.test_first_season_db import database as reward_database  # noqa: F401
from tests.territory.support import ownership as base


@pytest.fixture
async def database(reward_database):  # noqa: F811 - pytest fixture dependency
    return reward_database


class Lookup:
    async def find_by_ids(self, ids):
        return {i: TerritorySiteSnapshot(i, Decimal("37.501"), Decimal("127.002")) for i in ids}


async def page(database, member, **params):
    async with database() as db:
        await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        return await territory_owned.list_owned(db, member, Lookup(), **params)


async def test_member_all_pets_keyset_and_expiry_between_pages(database, actors, clock):
    (a, b), (pa, pa2, pb) = actors
    await start(database, clock)
    await actor(database, clock, a, pa)
    client = await base.begin(database, a, [pa2])
    await mark(database, clock, a, client, pa2, site_id=base.SITE2)
    first = await page(database, a, limit=1)
    assert first["total_count"] == 2 and first["items"][0]["site_id"] == base.SITE
    assert first["items"][0]["location"]["lat"] == Decimal("37.501")
    assert (await page(database, a, pet_id=pa))["total_count"] == 1
    assert (await page(database, a, pet_id=pa2))["items"][0]["site_id"] == base.SITE2
    assert (await page(database, b))["total_count"] == 0
    with pytest.raises(activity.ActivityNotFound):
        await page(database, a, pet_id=pb)
    # Expiring a preceding row would shift offset pages. A keyset still reaches SITE2.
    async with database() as db:
        occupied = await db.get(TerritoryOccupancy, base.SITE)
        occupied.expires_at = datetime.fromtimestamp((clock[0] + 1000) / 1000, UTC)
        await db.commit()
    clock[0] += 1000
    second = await page(database, a, cursor=first["next_cursor"], limit=1)
    assert second["total_count"] == 1 and second["next_cursor"] is None
    assert second["items"][0]["site_id"] == base.SITE2


async def test_takeover_moves_list_to_new_owner(database, actors, clock, monkeypatch):
    (a, b), (pa, _, pb) = actors
    await start(database, clock)
    await actor(database, clock, a, pa)
    rival = await actor(database, clock, b, pb)
    assert (await page(database, a))["total_count"] == 1
    assert (await page(database, b))["total_count"] == 0  # Pending photo is not possession.
    await certify(database, clock, rival, monkeypatch)
    assert (await page(database, a))["total_count"] == 0
    owned = await page(database, b)
    assert owned["total_count"] == 1
    assert owned["items"][0]["certification"] == "VERIFIED"
    assert owned["items"][0]["pet_id"] == pb


async def test_expiry_is_excluded_without_running_worker_or_mutating_state(database, actors, clock):
    (a, _), (pa, _, _) = actors
    await start(database, clock)
    await actor(database, clock, a, pa)
    async with database() as db:
        occupied = await db.get(TerritoryOccupancy, base.SITE)
        occupied.expires_at = datetime.fromtimestamp((clock[0] + 1000) / 1000, UTC)
        await db.commit()
    clock[0] += 999
    assert (await page(database, a))["total_count"] == 1
    clock[0] += 1
    assert (await page(database, a))["total_count"] == 0
    async with database() as db:
        assert await db.get(TerritoryOccupancy, base.SITE) is not None
        assert (await db.scalar(select(ActivityHoldingPeriod))).ended_ms is None


async def test_season_window_and_open_period_scope(database, actors, clock):
    (a, _), (pa, _, _) = actors
    await start(database, clock)
    await actor(database, clock, a, pa)
    assert (await page(database, a))["total_count"] == 1
    async with database() as db:
        season = await db.get(ActivitySeason, "first")
        clock[0] = season.ends_ms
    value = await page(database, a)
    assert value["status"] == "NO_ACTIVE_SEASON" and value["items"] == []
    # The reader hides the finished season even before the worker closes its periods.
    async with database() as db:
        assert (await db.scalar(select(ActivityHoldingPeriod))).ended_ms is None
        await activity_game.close_if_due(db, clock[0])
        await db.commit()
    clock[0] += 1000
    await start(database, clock, name="second")
    current = await page(database, a)
    assert current["status"] == "READY" and current["season_id"] == "second"
    assert current["total_count"] == 0 and current["items"] == []
    async with database() as db:
        assert (await db.scalar(select(ActivityHoldingPeriod))).ended_ms is not None
