"""Real bookmark DDL in disposable schemas; no game season or registered dogs."""

import os
import uuid
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from daengs_backend.config import settings
from daengs_backend.core.database import get_session, get_snapshot_session
from daengs_backend.core.subject import SubjectType
from daengs_backend.core.token import create_access_token
from daengs_backend.main import app
from daengs_backend.models.app_user import AppUser
from daengs_backend.services.territory_site_batch_lookup import get_territory_site_batch_lookup
from daengs_backend.services.territory_site_lookup import (
    TerritorySiteSnapshot,
    TerritorySiteUnavailableError,
)
from tests.territory.support.paths import REPO

MIGRATION = "db/migrations/2026-09-10_territory_bookmarks.sql"
VERIFY = "db/migrations/verify_2026-09-10_territory_bookmarks.sql"


async def sql_file(db, path):
    raw = (await (await db.connection()).get_raw_connection()).driver_connection
    sql = (REPO / path).read_text("utf-8")
    # Keep the fixture's enclosing transaction, including when replaying existing-volume SQL.
    sql = "\n".join(line for line in sql.splitlines() if line.strip() not in {"BEGIN;", "COMMIT;"})
    await raw.execute(sql)


@pytest.fixture
async def database():
    address = os.environ.get("TERRITORY_TEST_DATABASE_URL")
    if not address:
        pytest.skip("disposable local PostgreSQL not configured")
    url = make_url(address)
    if url.host not in {"127.0.0.1", "localhost"} or url.database != "claims_test":
        pytest.fail("only localhost/claims_test is allowed")
    admin = create_async_engine(url, poolclass=NullPool)
    schema = "bookmark_test_" + uuid.uuid4().hex
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": schema, "lock_timeout": "5000"}},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            raw = (await (await db.connection()).get_raw_connection()).driver_connection
            await raw.execute(
                (REPO / "db/init/02_trigger.sql").read_text("utf-8").split("DROP TRIGGER")[0]
            )
            await sql_file(db, "db/init/03_auth.sql")
            # The current AppUser mapper also includes columns installed by pets init.
            await sql_file(db, "db/init/05_pets.sql")
            await sql_file(db, MIGRATION)
            await sql_file(db, VERIFY)
            await db.commit()
        yield factory
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


def headers(member):
    return {"Authorization": "Bearer " + create_access_token(member, SubjectType.APP)}


@pytest.fixture
async def api(database, monkeypatch):
    members = [uuid.uuid4(), uuid.uuid4()]
    async with database() as db:
        db.add_all([AppUser(id=member, kakao_id=i + 1) for i, member in enumerate(members)])
        await db.commit()
    state = SimpleNamespace(
        members=members,
        sessions=[],
        lookup_calls=0,
        missing=False,
        unavailable=False,
        hook=None,
    )

    async def request_session():
        async with database() as db:
            state.sessions.append(db)
            yield db

    async def snapshot():
        async with database() as db:
            await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            state.sessions.append(db)
            yield db

    class Lookup:
        async def find_by_ids(self, ids):
            assert all(not db.in_transaction() for db in state.sessions)
            state.lookup_calls += 1
            if state.hook:
                await state.hook()
            if state.unavailable:
                raise TerritorySiteUnavailableError
            return (
                {}
                if state.missing
                else {site: TerritorySiteSnapshot(site, 37.5, 127.0) for site in ids}
            )

    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_session] = request_session
    app.dependency_overrides[get_snapshot_session] = snapshot
    app.dependency_overrides[get_territory_site_batch_lookup] = Lookup
    monkeypatch.setattr(settings, "activity_game_enabled", False)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            state.client = client
            yield state
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
