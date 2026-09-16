"""테스트 공용 **장치와 팩토리**. 시나리오 데이터는 여기 두지 않는다.

경계: **만드는 방법은 공유, 무엇을 만들지는 각 테스트가 소유.**
`seeded_places(rows)`가 행 목록을 인자로 받는 게 그 경계다 — 공용 데이터셋을 여기 두면
누가 행 하나 추가할 때마다 남의 정렬·집합 assert가 깨지고, 깨진 테스트를 읽어도
왜 그 데이터가 있는지 파일 안에서 안 보인다.

원본은 DAENGS_geo `tests/conftest.py` — walk/journey/provider 팩토리를 뺀 발췌다
(UPSTREAM.md). 그쪽 장치가 다시 필요해지면 경계(D-026)를 먼저 의심하라.
"""

import json
import math
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from daengs_place.core.config import settings


def daily_hours(*ranges: tuple[str, str]) -> str:
    """매일 같은 영업시간인 `place.hours` JSON. `daily_hours(("09:00", "18:00"))`"""
    weekly = {str(d): [list(r) for r in ranges] for d in range(7)}
    return json.dumps({"tz": "Asia/Seoul", "weekly": weekly})


# 동해 한복판. 실제 데이터·개발 시드와 절대 안 겹쳐서, 다른 행을 비키게 할 필요가 없다.
# (`UPDATE place SET active=false` 로 시드를 치우면 테이블 전체에 락이 걸리고,
#  그 상태로 테스트가 죽으면 다음 실행이 통째로 멈춘다 — 실제로 그렇게 됐었다.)
TEST_ORIGIN = (37.4979, 130.9000)
TEST_SOURCE = "test:conftest"


def place_row(
    source_id: str,
    name: str,
    *,
    east_m: int,
    kind: str = "hospital",
    tags: list[str] | tuple[str, ...] = (),
    hours: str | None = None,
) -> dict:
    """`place` 행 하나. 원점에서 동쪽으로 `east_m` 미터 — 거리 순서를 테스트가 직접 정한다.

    `hours=None` 이 공공데이터 기본값이다 (인허가 원천은 영업시간을 안 준다).
    태그는 파이썬 리스트로 나간다 — asyncpg 는 text[] 에 '{a,b}' 리터럴을 안 받는다.
    """
    return {
        "sid": source_id,
        "name": name,
        "kind": kind,
        "tags": list(tags),
        "hours": hours,
        "east_m": east_m,
    }


# ============================================================ DB 장치


#: 첫 연결 실패의 skip 사유. 한 번 못 붙었으면 같은 실행 안에서는 다시 시도하지 않는다.
_unreachable: str | None = None


@asynccontextmanager
async def db_session():
    """PostGIS 세션. 못 붙으면 skip.

    - 엔진을 매번 새로 만든다: `app.core.db` 의 전역 엔진은 import 시점 루프에 묶여
      pytest-asyncio 의 함수별 루프와 충돌해 **그냥 멈춘다**.
    - `pytest.fixture` 로 안 감싼다: async 픽스처도 같은 루프 스코프 문제를 탄다.
    - `statement_timeout`: 멈추더라도 테이블을 붙잡고 있지 않게.
    - **실패만 기억한다** (#519): Windows 는 닫힌 포트도 거절까지 약 2초라, 이 장치를 쓰는
      73개가 매번 4초씩 기다리다 skip 했다(약 5분). 성공한 연결은 위 이유로 매번 새로 연다.
    """
    global _unreachable
    if _unreachable is not None:
        pytest.skip(_unreachable)
    engine = create_async_engine(
        settings.sqlalchemy_url,
        poolclass=NullPool,
        connect_args={"server_settings": {"statement_timeout": "15000"}},
    )
    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        await session.close()
        await engine.dispose()
        _unreachable = f"PostGIS 없음 — place-db 를 띄운 뒤 재실행 ({type(exc).__name__})"
        pytest.skip(_unreachable)
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


def _lng_at(origin: tuple[float, float], east_m: int) -> float:
    """동쪽 미터 → 경도. 위도에 따라 경도 1도의 길이가 달라진다."""
    return origin[1] + east_m / (111_320 * math.cos(math.radians(origin[0])))


@asynccontextmanager
async def seeded_places(
    rows: list[dict], *, origin: tuple[float, float] = TEST_ORIGIN, source: str = TEST_SOURCE
):
    """`place_row()` 목록을 심고 세션을 넘긴다. 끝나면 **이 source 만** 지운다.

    다른 행은 건드리지 않는다 — 격리는 좌표로 한다.
    """
    async with db_session() as session:
        try:
            await session.execute(text("DELETE FROM place WHERE source = :s"), {"s": source})
            for r in rows:
                await session.execute(
                    text("""
                    INSERT INTO place (kind, name, address, phone, location, is_night, is_24h,
                                       hours, tags, source, source_id, active)
                    VALUES (:kind, :name, '테스트', '02-0',
                            ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,
                            false, false, CAST(:hours AS jsonb), CAST(:tags AS text[]),
                            :s, :sid, true)
                """),
                    {
                        "kind": r["kind"],
                        "name": r["name"],
                        "hours": r["hours"],
                        "tags": r["tags"],
                        "sid": r["sid"],
                        "s": source,
                        "lat": origin[0],
                        "lng": _lng_at(origin, r["east_m"]),
                    },
                )
            await session.commit()
            yield session
        finally:
            await session.rollback()
            await session.execute(text("DELETE FROM place WHERE source = :s"), {"s": source})
            await session.commit()
