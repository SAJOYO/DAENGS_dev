"""Actual member DDL and HTTP auth, isolated from shared databases."""

import os
import uuid
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from daengs_backend.core.database import get_session, get_snapshot_session
from daengs_backend.core.subject import SubjectType
from daengs_backend.core.token import create_access_token
from daengs_backend.main import app
from daengs_backend.models.app_user import AppUser
from daengs_backend.services.place_bookmark_lookup import (
    PlaceLookupUnavailable,
    get_place_bookmark_lookup,
)

ROOT = Path(__file__).resolve().parents[3]


def headers(owner):
    return {"Authorization": "Bearer " + create_access_token(owner, SubjectType.APP)}


async def sql_file(db, name):
    raw = (await (await db.connection()).get_raw_connection()).driver_connection
    sql = (ROOT / name).read_text("utf-8")
    await raw.execute(
        "\n".join(line for line in sql.splitlines() if line.strip() not in {"BEGIN;", "COMMIT;"})
    )


@pytest.fixture
async def database():
    address = os.environ.get("TERRITORY_TEST_DATABASE_URL")
    if not address:
        pytest.skip("disposable localhost/claims_test required")
    url = make_url(address)
    assert url.host == "127.0.0.1" and url.database == "claims_test"
    admin = create_async_engine(url, poolclass=NullPool)
    schema = "place_bookmark_test_" + uuid.uuid4().hex
    async with admin.begin() as db:
        await db.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        url, poolclass=NullPool, connect_args={"server_settings": {"search_path": schema}}
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            raw = (await (await db.connection()).get_raw_connection()).driver_connection
            await raw.execute(
                (ROOT / "db/init/02_trigger.sql").read_text("utf-8").split("DROP TRIGGER")[0]
            )
            for path in [
                "db/init/03_auth.sql",
                "db/init/05_pets.sql",
                # Pet 매퍼가 `identity_id` 를 들고 있습니다 (공동 돌봄 논리 연결).
                "db/init/25_pet_identities.sql",
                "db/migrations/2026-09-11_place_bookmarks.sql",
                "db/migrations/verify_2026-09-11_place_bookmarks.sql",
            ]:
                await sql_file(db, path)
            await db.commit()
        yield factory
    finally:
        await engine.dispose()
        async with admin.begin() as db:
            await db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


@pytest.fixture
async def api(database):
    members = [uuid.uuid4(), uuid.uuid4()]
    async with database() as db:
        db.add_all([AppUser(id=owner, kakao_id=i + 1) for i, owner in enumerate(members)])
        await db.commit()
    state = SimpleNamespace(
        members=members, sessions=[], unavailable=False, missing=False, calls=[], hook=None
    )
    request_session = ContextVar("bookmark_request_session")

    async def session():
        async with database() as db:
            state.sessions.append(db)
            request_session.set(db)
            yield db

    async def snapshot():
        async with database() as db:
            await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            state.sessions.append(db)
            request_session.set(db)
            yield db

    class Lookup:
        async def lookup(self, keys, filters):
            assert not request_session.get().in_transaction()
            state.calls.append((keys, filters))
            if state.hook:
                await state.hook()
            if state.unavailable:
                raise PlaceLookupUnavailable
            return {
                "filters": filters,
                "distance_available": False,
                "hits": []
                if state.missing
                else [{"place": {"key": k.model_dump(), "name": "저장 시설"}} for k in keys],
                "missing_keys": [k.model_dump() for k in keys] if state.missing else [],
            }

    previous = app.dependency_overrides.copy()
    app.dependency_overrides.update(
        {get_session: session, get_snapshot_session: snapshot, get_place_bookmark_lookup: Lookup}
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            state.client = client
            yield state
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
