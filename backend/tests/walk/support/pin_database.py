"""Shared pin database test builders; no test cases."""

import os
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from daengs_backend.config import settings
from tests.walk.support.entry_v2 import AT, OWNER, WALK
from tests.walk.support.paths import REPO as REPO_ROOT

ROOT = REPO_ROOT


@pytest.fixture
async def database(monkeypatch):
    address = os.environ.get("WALK_PIN_TEST_DATABASE_URL")
    if not address:
        pytest.skip("WALK_PIN_TEST_DATABASE_URL: disposable local DB not configured")
    url = make_url(address)
    if url.host not in {"localhost", "127.0.0.1"} or url.database != "walk_pin_test":
        pytest.fail("only localhost/walk_pin_test is allowed")
    schema = "walk_pin_" + uuid.uuid4().hex
    admin = create_async_engine(url, poolclass=NullPool)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": schema, "lock_timeout": "5000"}},
    )
    monkeypatch.setattr(settings, "walk_entry_v2_enabled", True)
    monkeypatch.setattr(settings, "walk_entry_v2_write_enabled", True)
    try:
        async with engine.begin() as connection:
            raw = (await connection.get_raw_connection()).driver_connection
            # Only external parent tables are minimal fixtures. Walk/entry/pin DDL is the real SQL.
            await raw.execute(
                "CREATE TABLE app_users (id UUID PRIMARY KEY); CREATE TABLE pets (id UUID PRIMARY KEY, app_user_id UUID REFERENCES app_users(id) ON DELETE CASCADE)"
            )
            for filename in [
                "db/init/06_walks.sql",
                "db/init/19_walk_entries.sql",
                "db/init/24_walk_entry_contexts.sql",
                "db/migrations/2026-09-09_walk_entry_pins.sql",
                "db/migrations/2026-09-09_walk_entry_pins.sql",
                "db/init/25_walk_entry_pins.sql",
                "db/migrations/verify_2026-09-09_walk_entry_pins.sql",
            ]:
                await raw.execute((ROOT / filename).read_text(encoding="utf-8"))
            await raw.execute("INSERT INTO app_users(id) VALUES ($1)", OWNER)
            await raw.execute(
                "INSERT INTO walks(id, app_user_id, client_session_id, started_at, ended_at) VALUES ($1,$2,$3,$4,$5)",
                WALK,
                OWNER,
                uuid.uuid4(),
                AT - timedelta(minutes=1),
                AT + timedelta(minutes=1),
            )
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()
