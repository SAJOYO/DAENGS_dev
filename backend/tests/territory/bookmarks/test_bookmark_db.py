"""Concurrent writes are checked against PostgreSQL, not an in-memory count mock."""

import asyncio
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from daengs_backend.models.app_user import AppUser
from daengs_backend.models.territory_bookmark import TerritoryBookmark
from daengs_backend.repositories import territory_bookmark as repo
from daengs_backend.services import territory_bookmark as service
from daengs_backend.services.territory_site_lookup import TerritorySiteSnapshot
from tests.territory.bookmarks.conftest import VERIFY, sql_file
from tests.territory.bookmarks.test_bookmark_api import SITE, site
from tests.territory.support.database import database as territory_database  # noqa: F401


async def seed(database, count=0):
    owner = uuid.uuid4()
    async with database() as db:
        db.add(AppUser(id=owner, kakao_id=123))
        await db.flush()
        db.add_all([TerritoryBookmark(app_user_id=owner, site_id=site(i)) for i in range(count)])
        await db.commit()
    return owner


@pytest.mark.parametrize("same_site", [True, False])
async def test_concurrent_saves_at_last_slot(database, same_site):
    owner = await seed(database, 19)
    barrier = asyncio.Barrier(2)

    class Lookup:
        async def find_by_ids(self, ids):
            # Both requests passed the initial 19-count check before either inserts.
            await asyncio.wait_for(barrier.wait(), timeout=5)
            return {sid: TerritorySiteSnapshot(sid, 37.5, 127.0) for sid in ids}

    async def save(sid):
        async with database() as db:
            return await service.save(db, owner, sid, Lookup())

    results = await asyncio.wait_for(
        asyncio.gather(save(site(20)), save(site(20 if same_site else 21)), return_exceptions=True),
        timeout=10,
    )
    successes = [row for row in results if not isinstance(row, BaseException)]
    assert len(successes) == (2 if same_site else 1), results
    assert all(row.total_count == 20 for row in successes)
    if same_site:
        assert successes[0].created_at == successes[1].created_at
    else:
        assert sum(isinstance(row, service.BookmarkLimitReached) for row in results) == 1
    async with database() as db:
        assert await repo.count(db, owner) == 20


async def test_insert_failure_rolls_back_and_releases_member(database, monkeypatch):
    owner = await seed(database)

    class Lookup:
        async def find_by_ids(self, ids):
            return {sid: TerritorySiteSnapshot(sid, 37.5, 127.0) for sid in ids}

    original = repo.insert

    async def fail(db, *args):
        await original(db, *args)
        raise RuntimeError("fail after flush")

    monkeypatch.setattr(repo, "insert", fail)
    async with database() as db:
        with pytest.raises(RuntimeError, match="fail after flush"):
            await service.save(db, owner, SITE, Lookup())
        assert not db.in_transaction()
    async with database() as db:
        assert await repo.count(db, owner) == 0
        assert (await service.remove(db, owner, SITE)).total_count == 0


async def test_database_uniqueness_member_fk_and_site_constraint(database):
    owner = await seed(database, 1)
    rows = [
        TerritoryBookmark(app_user_id=owner, site_id=site(0)),
        TerritoryBookmark(app_user_id=uuid.uuid4(), site_id=SITE),
        TerritoryBookmark(app_user_id=owner, site_id="not-a-site"),
    ]
    for row in rows:
        async with database() as db:
            db.add(row)
            with pytest.raises(IntegrityError):
                await db.flush()
    async with database() as db:
        assert await repo.count(db, owner) == 1


async def test_fresh_init_independently_satisfies_verifier(database):
    async with database() as db:
        await db.execute(text("DROP TABLE territory_bookmarks"))
        await sql_file(db, "db/init/33_territory_bookmarks.sql")
        await sql_file(db, VERIFY)
        await db.commit()


async def test_withdrawal_trigger_coexists_with_current_territory_schema(territory_database):  # noqa: F811
    # Full existing game/care dependency DDL has other withdrawal triggers.
    async with territory_database() as db:
        await sql_file(db, "db/init/33_territory_bookmarks.sql")
        owner = uuid.uuid4()
        db.add(AppUser(id=owner, kakao_id=543))
        await db.flush()
        db.add(TerritoryBookmark(app_user_id=owner, site_id=SITE))
        await db.commit()
        (await db.get(AppUser, owner)).status = "withdrawn"
        await db.commit()
        assert list(await db.scalars(select(TerritoryBookmark))) == []
