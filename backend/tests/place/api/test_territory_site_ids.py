"""ID lookup and existing nearby SQL in an isolated local PostGIS schema."""

import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from daengs_place.core.db import get_session
from daengs_place.main import app

SITE = "territory-site:hex-v1:140:324:777"


async def request(db, params, path="by-ids"):
    app.dependency_overrides[get_session] = lambda: db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.get(f"/territory/sites/{path}", params=params)
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"site_ids": "bad"},
        {"site_ids": "territory-site:hex-v1:115:1:1"},
        [("site_ids", SITE)] * 101,
    ],
)
async def test_batch_validation_requires_current_ids_and_bounded_count(params):
    assert (await request(object(), params)).status_code == 422


@pytest.fixture
async def site_db():
    address = os.environ.get("TERRITORY_TEST_DATABASE_URL")
    if not address:
        pytest.skip("disposable local PostGIS not configured")
    url = make_url(address)
    if url.host not in {"localhost", "127.0.0.1"} or url.database != "claims_test":
        pytest.fail("only localhost/claims_test is allowed")
    admin = create_async_engine(url, poolclass=NullPool)
    schema = "site_lookup_" + uuid.uuid4().hex
    async with admin.begin() as db:
        await db.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        url,
        poolclass=NullPool,
        connect_args={
            "server_settings": {"search_path": f"{schema},public", "statement_timeout": "5000"},
        },
    )
    try:
        async with engine.begin() as db:
            await db.execute(
                text("""CREATE TABLE territory_site (
                site_id text PRIMARY KEY, location geography(Point,4326) NOT NULL
            )""")
            )
            for site_id, lng in [
                (SITE, 127.0),
                (SITE + "0", 129.0),
                ("territory-site:hex-v1:115:1:1", 127.0),
            ]:
                await db.execute(
                    text("""INSERT INTO territory_site VALUES
                    (:id, ST_SetSRID(ST_MakePoint(:lng, 37.5),4326)::geography)"""),
                    {"id": site_id, "lng": lng},
                )
        async with async_sessionmaker(engine)() as db:
            yield db
    finally:
        await engine.dispose()
        async with admin.begin() as db:
            await db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def test_batch_reads_far_away_sites_deduplicates_and_omits_missing(site_db):
    response = await request(
        site_db, [("site_ids", i) for i in [SITE, SITE, SITE + "0", SITE + "1"]]
    )
    assert response.status_code == 200
    sites = response.json()["sites"]
    assert sites == [
        {"site_id": SITE, "lat": 37.5, "lng": 127.0},
        {"site_id": SITE + "0", "lat": 37.5, "lng": 129.0},
    ]


async def test_existing_nearby_query_still_filters_by_distance_and_generation(site_db):
    response = await request(site_db, {"lat": 37.5, "lng": 127.0, "radius_m": 50}, "nearby")
    assert response.status_code == 200
    assert response.json() == {
        "count": 1,
        "truncated": False,
        "sites": [{"site_id": SITE, "lat": 37.5, "lng": 127.0, "distance_m": 0.0}],
    }
