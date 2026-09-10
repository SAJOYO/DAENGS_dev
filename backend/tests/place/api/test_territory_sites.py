"""중립 점령지 앱 계약과 현행 140u 게임판 조회의 PostGIS 통합 테스트."""

import math

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from daengs_place.core.db import get_session
from daengs_place.main import app
from tests.place.support.database import TEST_ORIGIN, db_session

SOURCE = "test:territory-sites"


def _lng_at(east_m: int) -> float:
    return TEST_ORIGIN[1] + east_m / (
        111_320 * math.cos(math.radians(TEST_ORIGIN[0]))
    )


async def _seed(session, generations: list[tuple[str, int]]) -> None:
    for site_id, east_m in generations:
        await session.execute(
            text("""
                INSERT INTO territory_site (site_id, source, kind, location)
                VALUES (:site_id, :source, '한전주',
                        ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography)
            """),
            {
                "site_id": site_id,
                "source": SOURCE,
                "lat": TEST_ORIGIN[0],
                "lng": _lng_at(east_m),
            },
        )
    await session.commit()


async def _request(session, **params):
    async def override_session():
        yield session

    app.dependency_overrides[get_session] = override_session
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            return await client.get("/territory/sites/nearby", params=params)
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.mark.parametrize(
    ("seeded", "limit", "expected_count", "truncated"),
    [(3, 5, 3, False), (5, 5, 5, False), (6, 5, 5, True)],
)
async def test_nearby_orders_by_distance_and_reports_truncation(
    seeded: int,
    limit: int,
    expected_count: int,
    truncated: bool,
) -> None:
    async with db_session() as session:
        try:
            await _seed(
                session,
                [(f"territory-site:hex-v1:140:{index}:0", index * 10) for index in range(seeded)],
            )
            response = await _request(
                session,
                lat=TEST_ORIGIN[0],
                lng=TEST_ORIGIN[1],
                radius_m=500,
                limit=limit,
            )

            assert response.status_code == 200
            body = response.json()
            assert body["count"] == expected_count
            assert body["truncated"] is truncated
            assert len(body["sites"]) == expected_count
            assert [site["distance_m"] for site in body["sites"]] == sorted(
                site["distance_m"] for site in body["sites"]
            )
            assert set(body["sites"][0]) == {"site_id", "lat", "lng", "distance_m"}
        finally:
            await session.execute(
                text("DELETE FROM territory_site WHERE source = :source"),
                {"source": SOURCE},
            )
            await session.commit()


async def test_nearby_does_not_mix_a_legacy_grid_generation() -> None:
    async with db_session() as session:
        try:
            await _seed(
                session,
                [
                    ("territory-site:hex-v1:115:1:0", 10),
                    ("territory-site:hex-v1:140:2:0", 20),
                ],
            )
            response = await _request(
                session,
                lat=TEST_ORIGIN[0],
                lng=TEST_ORIGIN[1],
                radius_m=500,
            )

            assert response.status_code == 200
            assert [site["site_id"] for site in response.json()["sites"]] == [
                "territory-site:hex-v1:140:2:0"
            ]
        finally:
            await session.execute(
                text("DELETE FROM territory_site WHERE source = :source"),
                {"source": SOURCE},
            )
            await session.commit()
