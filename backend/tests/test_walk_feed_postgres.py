"""통합 산책 목록의 조건·정렬·합계 SQL 이 진짜 PostgreSQL 에서 맞는지 (`repositories/walk_group.py`).

가짜 저장소 테스트(`test_walk_feed.py`)는 조건을 파이썬으로 흉내 내므로 SQL 자체를 증명하지
못합니다. 시간대 경계(`timezone(tz, started_at)`)·최신 세대 `DISTINCT ON`·`IN` 서브쿼리 중복 제거는
여기서만 봅니다. 모든 쓰기는 한 트랜잭션 안에서 하고 rollback 합니다. loopback DB 만 만집니다.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from daengs_backend.repositories import walk_group as repo

_DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:5432/vectordb"
T0 = datetime(2026, 9, 10, 8, 0, tzinfo=UTC)


@pytest.fixture
async def db():
    dsn = os.environ.get("DAENGS_TEST_DATABASE_URL", _DEFAULT_DSN)
    if urlparse(dsn).hostname not in {"127.0.0.1", "localhost", "::1"}:
        pytest.fail("통합 산책 목록 SQL 증명은 loopback 이 아닌 DB 를 거부한다")
    engine = create_async_engine(make_url(dsn).set(drivername="postgresql+asyncpg"), poolclass=NullPool)
    try:
        conn = await engine.connect()
    # asyncpg 는 접속 단계의 인증 실패(`InvalidPasswordError` 등)를 SQLAlchemy 예외로 감싸지 않습니다 —
    # 5432 에 다른 프로젝트의 Postgres 가 떠 있는 PC 에서 skip 대신 오류가 나지 않게 같이 받습니다.
    except (OSError, SQLAlchemyError, asyncpg.PostgresError) as exc:
        await engine.dispose()
        pytest.skip(f"로컬 PostgreSQL 없음: {type(exc).__name__}")
    # 스키마 확인도 같은 트랜잭션 안에서 — 먼저 읽으면 autobegin 이 걸려 begin() 을 못 부릅니다.
    transaction = await conn.begin()
    present = (await conn.execute(text("SELECT to_regclass('public.walk_analyses'), to_regclass('public.walk_pets')"))).one()
    if any(v is None for v in present):
        await transaction.rollback()
        await conn.close()
        await engine.dispose()
        pytest.skip("로컬 PostgreSQL 에 산책 스키마가 없다")
    session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await conn.close()
        await engine.dispose()


async def _user(session: AsyncSession) -> uuid.UUID:
    uid = uuid.uuid4()
    await session.execute(
        text("INSERT INTO app_users (id, kakao_id, status) VALUES (:id, :kakao, 'active')"),
        {"id": uid, "kakao": uuid.uuid4().int % 10**12},
    )
    return uid


async def _pet(session: AsyncSession, owner: uuid.UUID) -> uuid.UUID:
    pid = uuid.uuid4()
    await session.execute(
        text("INSERT INTO pets (id, app_user_id, name, breed) VALUES (:id, :owner, '맥스', '믹스')"),
        {"id": pid, "owner": owner},
    )
    return pid


async def _walk(
    session: AsyncSession,
    owner: uuid.UUID,
    pets: list[uuid.UUID],
    started: datetime,
    *,
    minutes: int = 30,
    weather: int | None = None,
    walk_id: uuid.UUID | None = None,
) -> uuid.UUID:
    wid = walk_id or uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO walks (id, app_user_id, client_session_id, started_at, ended_at, weather_code)"
            " VALUES (:id, :owner, :client, :started, :ended, :weather)"
        ),
        {
            "id": wid,
            "owner": owner,
            "client": uuid.uuid4(),
            "started": started,
            "ended": started + timedelta(minutes=minutes),
            "weather": weather,
        },
    )
    for pet in pets:
        await session.execute(text("INSERT INTO walk_pets (walk_id, pet_id) VALUES (:w, :p)"), {"w": wid, "p": pet})
    return wid


async def _analysis(session: AsyncSession, walk: uuid.UUID, distance: int, derived: datetime) -> None:
    await session.execute(
        text(
            "INSERT INTO walk_analyses (walk_id, input_fingerprint, point_count, facts_record_version,"
            " calculation_version, receipt_version, observation_version, moving_distance_m, moving_s, stop_count,"
            " facts, measurement_receipt, motion_events, micro_observations, derived_at)"
            " VALUES (:w, :fp, 0, 1, 1, 1, 1, :d, 60, 0, '{}', '{}', '[]', '[]', :at)"
        ),
        {"w": walk, "fp": "sha256:" + uuid.uuid4().hex + uuid.uuid4().hex, "d": distance, "at": derived},
    )


async def test_기간과_월은_요청한_시간대로_자른다(db: AsyncSession):
    owner = await _user(db)
    pet = await _pet(db, owner)
    late = await _walk(db, owner, [pet], datetime(2026, 9, 10, 15, 30, tzinfo=UTC))  # KST 09-11 00:30
    await _walk(db, owner, [pet], datetime(2026, 8, 31, 14, 0, tzinfo=UTC))  # KST 08-31 23:00

    kst_midnight = datetime(2026, 9, 11, tzinfo=ZoneInfo("Asia/Seoul"))
    f = repo.WalkFeedFilter(pet_ids=(pet,), started_from=kst_midnight, tz="Asia/Seoul")
    assert [w.id for w in await repo.list_feed_page(db, f, limit=10)] == [late]

    september_kst = repo.WalkFeedFilter(pet_ids=(pet,), months=(9,), tz="Asia/Seoul")
    assert [w.id for w in await repo.list_feed_page(db, september_kst, limit=10)] == [late]
    august_utc = repo.WalkFeedFilter(pet_ids=(pet,), months=(8,), tz="UTC")
    assert len(await repo.list_feed_page(db, august_utc, limit=10)) == 1


async def test_날씨_보호자_제외_조건(db: AsyncSession):
    a = await _user(db)
    b = await _user(db)
    pet = await _pet(db, a)
    clear = await _walk(db, a, [pet], T0, weather=0)
    missing = await _walk(db, b, [pet], T0 + timedelta(hours=1))
    rainy = await _walk(db, b, [pet], T0 + timedelta(hours=2), weather=61)

    async def found(**kw) -> set[uuid.UUID]:
        return {w.id for w in await repo.list_feed_page(db, repo.WalkFeedFilter(pet_ids=(pet,), **kw), limit=10)}

    assert await found(weather_codes=(0,)) == {clear}
    assert await found(weather_missing=True) == {missing}
    assert await found(weather_codes=(61,), weather_missing=True) == {missing, rainy}
    assert await found(actor_ids=(b,)) == {missing, rainy}
    assert await found(actor_ids=()) == set()
    assert await found(exclude_actor=b) == {clear}


async def test_두_강아지에_태그돼도_한_번_같은_시각은_id_로_끊는다(db: AsyncSession):
    owner = await _user(db)
    first = await _pet(db, owner)
    second = await _pet(db, owner)
    ids = sorted((uuid.uuid4() for _ in range(4)), reverse=True)
    for wid in ids:
        await _walk(db, owner, [first, second], T0, walk_id=wid)

    f = repo.WalkFeedFilter(pet_ids=(first, second))
    page1 = await repo.list_feed_page(db, f, limit=2)
    page2 = await repo.list_feed_page(db, f, limit=2, before=(page1[-1].started_at, page1[-1].id))
    assert [w.id for w in page1 + page2] == ids
    assert (await repo.feed_totals(db, f)).count == 4


async def test_합계는_최신_세대_거리와_시간을_더한다(db: AsyncSession):
    owner = await _user(db)
    pet = await _pet(db, owner)
    measured = await _walk(db, owner, [pet], T0, minutes=30)
    await _analysis(db, measured, 900, T0 + timedelta(hours=1))
    await _analysis(db, measured, 1234, T0 + timedelta(hours=2))
    await _walk(db, owner, [pet], T0 - timedelta(days=1), minutes=45)  # 계산 전

    totals = await repo.feed_totals(db, repo.WalkFeedFilter(pet_ids=(pet,)))
    assert totals == repo.WalkFeedTotals(count=2, distance_m=1234, duration_s=30 * 60 + 45 * 60)
    empty = await repo.feed_totals(db, repo.WalkFeedFilter(pet_ids=(pet,), weather_codes=(95,)))
    assert empty == repo.WalkFeedTotals(0, 0, 0)
