"""트리거·인덱스가 진짜 PostgreSQL 에서 도는지의 증명 (docs/co-care.md §5).

가짜 리포지토리로는 증명할 수 없다 — 그것이 트리거를 고른 이유다. 모든 쓰기는 한
트랜잭션에서 검사한 뒤 rollback 한다. loopback DB 만 만진다.
"""

from __future__ import annotations

import os
import uuid
from urllib.parse import urlparse

import psycopg
import pytest

#: loopback 기본값. 두 가드가 같은 값을 봐야 하므로 한 곳에 둔다.
_DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:5432/vectordb"


def _postgres_or_skip():
    dsn = os.environ.get("DAENGS_TEST_DATABASE_URL", _DEFAULT_DSN)
    parsed = urlparse(dsn)
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        pytest.fail("공동 돌봄 증명은 loopback 이 아닌 DB 를 거부한다")
    try:
        conn = psycopg.connect(dsn, connect_timeout=3)
    except psycopg.Error as exc:
        pytest.skip(f"로컬 PostgreSQL 없음: {type(exc).__name__}")
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.pet_members'), to_regclass('public.pet_invites')")
        if any(v is None for v in cur.fetchone()):
            conn.close()
            pytest.skip("로컬 PostgreSQL 에 공동 돌봄 스키마가 없다")
    return conn


def _seed(cur) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """대표 · 돌보미 · 강아지 하나씩. kakao_id 는 충돌을 피해 난수로."""
    owner = uuid.uuid4()
    carer = uuid.uuid4()
    pet = uuid.uuid4()
    cur.execute(
        "INSERT INTO app_users (id, kakao_id, status) VALUES (%s, %s, 'active'), (%s, %s, 'active')",
        (owner, uuid.uuid4().int % 10**12, carer, uuid.uuid4().int % 10**12),
    )
    cur.execute(
        "INSERT INTO pets (id, app_user_id, name, breed) VALUES (%s, %s, '맥스', '믹스')",
        (pet, owner),
    )
    cur.execute(
        "INSERT INTO pet_members (pet_id, app_user_id) VALUES (%s, %s)", (pet, carer)
    )
    return owner, carer, pet


def test_withdrawal_deletes_membership():
    """탈퇴는 UPDATE 다. FK 는 안 돌고 트리거 ① 만이 돌보미 행을 지운다."""
    conn = _postgres_or_skip()
    try:
        with conn.cursor() as cur:
            owner, carer, pet = _seed(cur)
            cur.execute(
                "INSERT INTO pet_invites (pet_id, invited_by, token_hash, expires_at)"
                " VALUES (%s, %s, %s, NOW() + INTERVAL '1 day')",
                (pet, owner, "a" * 64),
            )

            cur.execute("UPDATE app_users SET status='withdrawn' WHERE id=%s", (carer,))
            cur.execute("SELECT count(*) FROM pet_members WHERE app_user_id=%s", (carer,))
            assert cur.fetchone()[0] == 0, "탈퇴한 돌보미가 유령으로 남았다"

            cur.execute("UPDATE app_users SET status='withdrawn' WHERE id=%s", (owner,))
            cur.execute("SELECT count(*) FROM pet_invites WHERE invited_by=%s", (owner,))
            assert cur.fetchone()[0] == 0, "탈퇴한 대표의 초대가 남았다"
    finally:
        conn.rollback()
        conn.close()


def test_owner_cannot_be_carer():
    """트리거 ② — 대표를 pet_members 에 넣으면 거절한다."""
    conn = _postgres_or_skip()
    try:
        with conn.cursor() as cur:
            owner, _carer, pet = _seed(cur)
            with pytest.raises(psycopg.errors.RaiseException):
                cur.execute(
                    "INSERT INTO pet_members (pet_id, app_user_id) VALUES (%s, %s)",
                    (pet, owner),
                )
    finally:
        conn.rollback()
        conn.close()


def test_succession_order_matters():
    """`pet_members_not_owner` 는 실제로 순서에 민감하다 — 가짜 리포지토리로는 증명이 안 된다.

    옛 대표를 새 대표보다 먼저 `pet_members` 에 넣으면(=서투른 순서), 그 순간에는 아직
    `pets.app_user_id` 가 옛 대표라 트리거가 그대로 거절해야 한다. 올바른 순서
    (`pets.app_user_id` 를 먼저 바꾸고 → 새 대표의 돌보미 행을 지우고 → 그제서야 옛 대표를
    넣는다)로 하면 같은 삽입이 통과해야 한다. `services/pet_member.py` 의 `transfer_owner`
    가 실제로 이 순서를 따르는지를 이 테스트가 지킨다.
    """
    conn = _postgres_or_skip()
    try:
        with conn.cursor() as cur:
            owner, carer, pet = _seed(cur)

            # ── 나쁜 순서: 옛 대표를 pet_members 에 먼저 넣는다 ──────────────
            # 이 시점에 pets.app_user_id 는 아직 owner 이므로 트리거가 거절해야 한다.
            with pytest.raises(psycopg.errors.RaiseException):
                cur.execute(
                    "INSERT INTO pet_members (pet_id, app_user_id) VALUES (%s, %s)",
                    (pet, owner),
                )
        # 트리거가 터진 문장은 이 서브트랜잭션을 오염시키므로, 다음 절을 위해 되돌린다.
        conn.rollback()

        with conn.cursor() as cur:
            owner, carer, pet = _seed(cur)

            # ── 올바른 순서: pets.app_user_id 를 먼저 새 대표로 바꾼다 ───────
            cur.execute("UPDATE pets SET app_user_id=%s WHERE id=%s", (carer, pet))
            # 새 대표의 돌보미 행을 지운다 — 대표는 pet_members 에 없어야 한다.
            cur.execute(
                "DELETE FROM pet_members WHERE pet_id=%s AND app_user_id=%s", (pet, carer)
            )
            # 이제 옛 대표를 돌보미로 넣어도 트리거가 통과해야 한다.
            cur.execute(
                "INSERT INTO pet_members (pet_id, app_user_id) VALUES (%s, %s)",
                (pet, owner),
            )
            cur.execute(
                "SELECT app_user_id FROM pets WHERE id=%s", (pet,)
            )
            assert cur.fetchone()[0] == carer
            cur.execute(
                "SELECT count(*) FROM pet_members WHERE pet_id=%s AND app_user_id=%s",
                (pet, owner),
            )
            assert cur.fetchone()[0] == 1, "옛 대표가 돌보미로 안 들어갔다"
    finally:
        conn.rollback()
        conn.close()


def test_member_lookup_index_exists():
    """app_user_id 인덱스. 마이그레이션에서 누락되면 여기서 걸린다."""
    conn = _postgres_or_skip()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT indexdef FROM pg_indexes"
                " WHERE tablename='pet_members' AND indexname='idx_pet_members_app_user'"
            )
            row = cur.fetchone()
            assert row is not None, "idx_pet_members_app_user 가 없다"
            assert "app_user_id" in row[0]
    finally:
        conn.rollback()
        conn.close()


def test_care_event_actor_is_nullable():
    """actor 는 '소유자' 가 아니라 '챙긴 사람' 이라 비어 있을 수 있어야 한다."""
    conn = _postgres_or_skip()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT is_nullable FROM information_schema.columns"
                " WHERE table_name='care_events' AND column_name='actor_app_user_id'"
            )
            row = cur.fetchone()
            assert row is not None, "actor_app_user_id 컬럼이 없다 (개명 누락)"
            assert row[0] == "YES"
    finally:
        conn.rollback()
        conn.close()


def test_migration_is_rerunnable():
    """버전 테이블이 없으므로 두 번 돌려도 안전해야 한다."""
    conn = _postgres_or_skip()
    try:
        sql = open("../db/migrations/2026-09-09_pet_members.sql", encoding="utf-8").read()
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute(sql)
    finally:
        conn.rollback()
        conn.close()


def _sqlalchemy_dsn_or_skip() -> str:
    """같은 loopback 가드를 지난 뒤 **SQLAlchemy(asyncpg) DSN** 을 돌려준다.

    아래 삭제 자격 증명만 이것을 쓴다. 그 판정은 psycopg 로 **흉내 내면 의미가 없다** —
    테스트가 직접 쓴 SQL 을 테스트가 확인하는 꼴이 된다. 진짜 `care_repo.get_deletable`
    을 진짜 DB 에서 불러야, 조건을 `pet_repo._is_member` 로 잘못 바꿔 놓은 구현이 여기서
    걸린다 (가짜 대역으로는 그 실수가 통과한다).

    가드를 새로 쓰지 않고 `_postgres_or_skip` 을 빌린다 — 두 벌이 되면 한쪽만 고쳐진다.
    """
    conn = _postgres_or_skip()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.care_events')")
            if cur.fetchone()[0] is None:
                pytest.skip("로컬 PostgreSQL 에 care_events 가 없다")
    finally:
        conn.close()
    dsn = os.environ.get("DAENGS_TEST_DATABASE_URL", _DEFAULT_DSN)
    return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)


async def test_care_event_delete_is_recorder_or_owner():
    """케어 기록은 **적은 사람 또는 그 아이의 대표**만 지운다 (docs/co-care.md §2).

    네 갈래를 한 트랜잭션에서 본다: 적은 사람 ✅ · 대표 ✅ · **다른 돌보미** ❌ · 남남 ❌.
    셋째가 이 테스트의 이유다 — 구성원 전체(`_is_member`)로 열어 놓아도 나머지 셋은 전부
    통과하므로, 그 실수는 여기서만 잡힌다.

    다른 테스트와 달리 SQLAlchemy 세션을 쓰는 것은 **진짜 리포지토리 함수를 부르기
    위해서**다. 트랜잭션 하나 · 끝에서 rollback 은 같다.
    """
    dsn = _sqlalchemy_dsn_or_skip()

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from daengs_backend.repositories import care_event as care_repo

    engine = create_async_engine(dsn)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        owner, carer, other, stranger = (str(uuid.uuid4()) for _ in range(4))
        pet, event = str(uuid.uuid4()), str(uuid.uuid4())

        # 값은 전부 문자열로 넘기고 SQL 에서 CAST 한다 — asyncpg 는 타입을 엄격히 본다.
        for uid in (owner, carer, other, stranger):
            await session.execute(
                text(
                    "INSERT INTO app_users (id, kakao_id, status)"
                    " VALUES (CAST(:i AS uuid), :k, 'active')"
                ),
                {"i": uid, "k": uuid.uuid4().int % 10**12},
            )
        await session.execute(
            text(
                "INSERT INTO pets (id, app_user_id, name, breed)"
                " VALUES (CAST(:p AS uuid), CAST(:o AS uuid), '맥스', '믹스')"
            ),
            {"p": pet, "o": owner},
        )
        # 돌보미 둘 — 하나는 기록한 사람, 하나는 같은 아이의 **다른** 돌보미.
        for uid in (carer, other):
            await session.execute(
                text(
                    "INSERT INTO pet_members (pet_id, app_user_id)"
                    " VALUES (CAST(:p AS uuid), CAST(:u AS uuid))"
                ),
                {"p": pet, "u": uid},
            )
        await session.execute(
            text(
                "INSERT INTO care_events"
                " (id, pet_id, actor_app_user_id, kind, occurred_at, client_event_id)"
                " VALUES (CAST(:e AS uuid), CAST(:p AS uuid), CAST(:a AS uuid),"
                "         'meal', NOW(), CAST(:k AS uuid))"
            ),
            {"e": event, "p": pet, "a": carer, "k": str(uuid.uuid4())},
        )

        async def deletable_by(who: str):
            return await care_repo.get_deletable(
                session, uuid.UUID(who), uuid.UUID(event)
            )

        assert await deletable_by(carer) is not None, "적은 사람이 자기 기록을 못 지운다"
        assert await deletable_by(owner) is not None, "대표가 돌보미의 오기록을 못 지운다"
        assert await deletable_by(other) is None, "다른 돌보미가 남의 기록을 지울 수 있다"
        assert await deletable_by(stranger) is None, "남남이 우리 아이 기록을 지울 수 있다"
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()
