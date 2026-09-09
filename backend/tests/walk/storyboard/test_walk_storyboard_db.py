"""Opt-in loopback PostgreSQL integration: real migrations, HTTP upload/finalize, and race fencing."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import asyncpg
import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import AppPrincipal, CurrentAppUser
from daengs_backend.routers import walk, walk_entry, walk_storyboard
from tests.walk.support.paths import REPO as REPO_ROOT

REPO = REPO_ROOT


@pytest.fixture
async def database():
    dsn = os.environ.get("LIVE_STORYBOARD_TEST_DSN")
    if not dsn:
        pytest.skip("set LIVE_STORYBOARD_TEST_DSN to a disposable loopback PostgreSQL")
    assert urlsplit(dsn).hostname in {"localhost", "127.0.0.1", "::1"}
    schema = "storyboard_test_" + uuid.uuid4().hex
    conn = await asyncpg.connect(dsn)
    await conn.execute(f'CREATE SCHEMA "{schema}"')
    await conn.execute(f'SET search_path TO "{schema}"')
    await conn.execute(
        "CREATE TABLE app_users (id uuid PRIMARY KEY); CREATE TABLE pets (id uuid PRIMARY KEY, app_user_id uuid)"
    )
    for file in (
        "db/init/06_walks.sql",
        "db/init/19_walk_entries.sql",
        "db/migrations/2026-09-05_walk_storyboards.sql",
        "db/migrations/2026-09-05_walk_storyboards.sql",
    ):
        await conn.execute((REPO / file).read_text(encoding="utf-8"))
    engine = create_async_engine(
        dsn.replace("postgresql://", "postgresql+asyncpg://"),
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": schema}},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield conn, factory
    finally:
        await engine.dispose()
        # Only this fixture's freshly created, UUID-named schema can be removed.
        assert schema.startswith("storyboard_test_") and len(schema) == 48
        await conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await conn.close()


async def test_real_upload_finalize_entries_revision_race_and_cascade(database):
    conn, factory = database
    owner, local, entry = [uuid.uuid4() for _ in range(3)]
    await conn.execute("INSERT INTO app_users VALUES ($1)", owner)
    app = FastAPI()
    for router in (walk.router, walk_entry.router, walk_storyboard.router):
        app.include_router(router)

    async def sessions():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_session] = sessions
    app.dependency_overrides[CurrentAppUser.__metadata__[0].dependency] = lambda: AppPrincipal(
        app_user_id=owner
    )
    app.dependency_overrides[walk.get_walk_weather_lookup] = lambda: AsyncMock(return_value=None)
    entered, release = asyncio.Event(), asyncio.Event()
    pause = False

    async def lookup(selection):
        if pause:
            entered.set()
            await release.wait()
        return {
            a["id"]: {"facts": [], "sources": [{"source": "test-place", "status": "unavailable"}]}
            for a in selection["anchors"]
        }

    app.dependency_overrides[walk_storyboard.get_context_lookup] = lambda: lookup
    start = datetime(2026, 9, 5, tzinfo=UTC)
    points = [
        {
            "client_seq": i,
            "chain_index": 0,
            "at": (start + timedelta(seconds=i * 10)).isoformat(),
            "lat": 37.5,
            "lng": 127 + i * 0.000113,
            "accuracy_m": 5,
        }
        for i in range(101)
    ]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        uploaded = await client.post(
            "/app/walks",
            json={
                "client_session_id": str(local),
                "pet_ids": [],
                "started_at": start.isoformat(),
                "ended_at": (start + timedelta(seconds=1000)).isoformat(),
                "points": points,
            },
        )
        assert uploaded.status_code == 201, uploaded.text
        walk_id = uploaded.json()["id"]
        path = f"/app/walks/{walk_id}"
        finalized = await client.post(
            path + "/finalize", json={"expected_point_count": 101, "terminal_client_seq": 100}
        )
        assert finalized.status_code in (200, 201), finalized.text
        first = await client.post(path + "/storyboard", json={"expected_entries": {}})
        assert first.status_code == 200 and first.json()["status"] == "ready", first.text
        assert not first.json()["bundle"]["synthetic"]
        # Existing completed requests are idempotent even through PostgreSQL JSONB serialization.
        assert (
            await client.post(path + "/storyboard", json={"expected_entries": {}})
        ).json() == first.json()
        pause = True
        old = asyncio.create_task(
            client.post(path + "/storyboard", json={"expected_entries": {}, "refresh": True})
        )
        await asyncio.wait_for(entered.wait(), 5)
        # An entry mutation can commit while environment I/O is waiting: the Walk lock was released.
        written = await asyncio.wait_for(
            client.put(
                path + f"/entries/{entry}",
                json={
                    "expected_revision": 0,
                    "mutation_id": str(uuid.uuid4()),
                    "content": {
                        "kind": "note",
                        "note": "원본 정정",
                        "recorded_at": (start + timedelta(seconds=500)).isoformat(),
                    },
                },
            ),
            5,
        )
        assert written.status_code == 200, written.text
        pause = False
        newer = await client.post(path + "/storyboard", json={"expected_entries": {str(entry): 1}})
        assert newer.json()["status"] == "ready", newer.text
        release.set()
        delayed = await old
        assert delayed.json()["generation"] == newer.json()["generation"]
        latest = (await client.get(path + "/storyboard")).json()
        assert latest == newer.json()
        assert any(s["id"] == f"entry:{entry}" for s in latest["bundle"]["scenes"])
        detailed = (
            await client.get(path + "/storyboard?bundle_format=walk-storyboard-candidates-v2")
        ).json()
        assert detailed["generation"] == latest["generation"]
        assert detailed["bundle"]["format"] == "walk-storyboard-candidates-v2"
        assert detailed["bundle"]["selection"]["minimum_met"]
        recorded = next(s for s in detailed["bundle"]["scenes"] if s["entry"])
        assert recorded["entry"] == {"entry_id": str(entry), "revision": 1, "pet_id": None}
        assert (
            await conn.fetchval("SELECT bundle->>'format' FROM walk_storyboards LIMIT 1")
            == "walk-storyboard-candidates-v2"
        )
        removed = await client.delete(
            path + f"/entries/{entry}",
            params={"expected_revision": 1, "mutation_id": str(uuid.uuid4())},
        )
        assert removed.status_code == 200
        assert (await client.get(path + "/storyboard")).json()["status"] == "stale"
        rebuilt = (
            await client.post(path + "/storyboard", json={"expected_entries": {str(entry): 2}})
        ).json()
        assert rebuilt["status"] == "ready"
        assert not any(s["id"] == f"entry:{entry}" for s in rebuilt["bundle"]["scenes"])
        assert (await client.get(f"/app/walks/{uuid.uuid4()}/storyboard")).status_code == 404
        await conn.execute("DELETE FROM app_users WHERE id=$1", owner)
        assert await conn.fetchval("SELECT count(*) FROM walk_storyboards") == 0


async def test_history_query_only_uses_three_prior_single_pet_owned_walks(database):
    from daengs_backend.repositories.walk import get_owned_for_update
    from daengs_backend.repositories.walk_storyboard import reference_walks

    conn, factory = database
    owner, other, pet, pet2, current = [uuid.uuid4() for _ in range(5)]
    await conn.executemany("INSERT INTO app_users VALUES ($1)", [(owner,), (other,)])
    await conn.executemany("INSERT INTO pets VALUES ($1,$2)", [(pet, owner), (pet2, owner)])
    start = datetime(2026, 9, 5, tzinfo=UTC)
    wanted = []
    for i in range(8):
        walk_id = current if i == 0 else uuid.uuid4()
        who = other if i == 1 else owner
        at = start - timedelta(days=i)
        await conn.execute(
            "INSERT INTO walks (id, app_user_id, client_session_id, started_at, ended_at, analysis_state) VALUES ($1,$2,$3,$4,$5,'derived')",
            walk_id,
            who,
            uuid.uuid4(),
            at,
            at + timedelta(minutes=1),
        )
        await conn.execute("INSERT INTO walk_pets VALUES ($1,$2)", walk_id, pet)
        if i == 2:
            await conn.execute("INSERT INTO walk_pets VALUES ($1,$2)", walk_id, pet2)
        if i in (3, 4, 5):
            wanted.append(walk_id)
    async with factory() as db:
        walk = await get_owned_for_update(db, owner, current)
        assert [w.id for w in await reference_walks(db, walk)] == wanted
