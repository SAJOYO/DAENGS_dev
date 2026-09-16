"""Disposable claims database and actors; fixtures open only when requested."""

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from daengs_backend.models import AppUser, Pet
from tests.territory.support.paths import REPO as ROOT


@pytest.fixture
async def database():
    address = os.environ.get("TERRITORY_TEST_DATABASE_URL")
    if not address:
        pytest.skip("disposable local PostgreSQL not configured")
    url = make_url(address)
    if url.host not in {"127.0.0.1", "localhost"} or url.database != "claims_test":
        pytest.fail("only localhost/claims_test is allowed")
    admin = create_async_engine(url, poolclass=NullPool)
    schema = "territory_test_" + uuid.uuid4().hex
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": schema, "lock_timeout": "5000"}},
    )
    try:
        async with engine.begin() as connection:
            raw = (await connection.get_raw_connection()).driver_connection
            trigger = (ROOT / "db/init/02_trigger.sql").read_text(encoding="utf-8")
            await raw.execute(trigger.split("DROP TRIGGER")[0])
            # Claim admission now reads pet_members (owner OR caregiver). Build the
            # current dependency schema even for tests using only primary owners.
            for file in (
                "03_auth.sql",
                "05_pets.sql",
                "08_territory_visits.sql",
                "23_care_events.sql",
                "24_pet_members.sql",
                # Pet 매퍼가 `identity_id` 를 들고 있어서 이 장이 없으면 ORM INSERT 가
                # UndefinedColumnError 로 죽습니다 (공동 돌봄 논리 연결).
                "25_pet_identities.sql",
            ):
                await raw.execute((ROOT / "db/init" / file).read_text(encoding="utf-8"))
            migration = (ROOT / "db/migrations/2026-09-05_territory_claims.sql").read_text(
                encoding="utf-8"
            )
            await raw.execute(migration)
            await raw.execute(migration)  # Existing-volume replay.
            await raw.execute(
                (ROOT / "db/init/20_territory_claims.sql").read_text(encoding="utf-8")
            )
            await raw.execute(
                (ROOT / "db/migrations/verify_2026-09-05_territory_claims.sql").read_text(
                    encoding="utf-8"
                )
            )
            certified = (ROOT / "db/migrations/2026-09-08_certified_territory.sql").read_text(
                encoding="utf-8"
            )
            await raw.execute(certified)
            await raw.execute(certified)
            await raw.execute(
                (ROOT / "db/init/24_certified_territory.sql").read_text(encoding="utf-8")
            )
            await raw.execute(
                (ROOT / "db/migrations/verify_2026-09-08_certified_territory.sql").read_text(
                    encoding="utf-8"
                )
            )
            await raw.execute(
                (ROOT / "db/migrations/2026-09-10_territory_expiry.sql").read_text("utf-8")
            )
        factory = async_sessionmaker(engine, expire_on_commit=False)
        yield factory
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            # The name is generated above, never read from configuration or user input.
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


@pytest.fixture
async def actors(database):
    owners = [uuid.uuid4(), uuid.uuid4()]
    pets = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    async with database() as db:
        db.add_all([AppUser(id=owner, kakao_id=index + 1) for index, owner in enumerate(owners)])
        await db.flush()
        db.add_all(
            [
                Pet(
                    id=pet,
                    app_user_id=owners[0 if index < 2 else 1],
                    name=f"dog{index}",
                    breed="mixed",
                )
                for index, pet in enumerate(pets)
            ]
        )
        await db.commit()
    return owners, pets
