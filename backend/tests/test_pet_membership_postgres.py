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

#: 첫 연결 실패의 skip 사유 (#519). 못 붙는 PC 에서 테스트마다 3초씩 기다리지 않게 한다.
_unreachable: str | None = None


def _postgres_or_skip():
    global _unreachable
    dsn = os.environ.get("DAENGS_TEST_DATABASE_URL", _DEFAULT_DSN)
    parsed = urlparse(dsn)
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        pytest.fail("공동 돌봄 증명은 loopback 이 아닌 DB 를 거부한다")
    if _unreachable is not None:
        pytest.skip(_unreachable)
    try:
        conn = psycopg.connect(dsn, connect_timeout=3)
    except psycopg.Error as exc:
        _unreachable = f"로컬 PostgreSQL 없음: {type(exc).__name__}"
        pytest.skip(_unreachable)
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


def test_withdrawal_nulls_care_event_actor():
    """탈퇴하면 **남의 집** 케어 로그에서도 그 사람의 id 가 사라진다.

    `care_events.actor_app_user_id` 의 FK 는 `ON DELETE SET NULL` 이지만 탈퇴가 `app_users`
    행을 안 지우므로 **안 돈다** — 비우는 것은 트리거 ① 뿐이고, 이것이 그 유일한 증명이다.
    돌보미가 대표의 강아지에 약을 적고 떠나면, 전에는 그 id 가 대표의 화면 뒤에 영원히
    남았다 (공동 돌봄 이전에는 케어 기록이 대표 것이라 대표 탈퇴와 함께 사라졌다).

    **행은 남아야 한다** — "그날 약을 먹은 사실" 은 강아지의 것이다 (결정 ①).
    """
    conn = _postgres_or_skip()
    try:
        with conn.cursor() as cur:
            _owner, carer, pet = _seed(cur)
            event = uuid.uuid4()
            cur.execute(
                "INSERT INTO care_events"
                " (id, pet_id, actor_app_user_id, kind, occurred_at, client_event_id)"
                " VALUES (%s, %s, %s, 'medication', NOW(), %s)",
                (event, pet, carer, uuid.uuid4()),
            )

            cur.execute("UPDATE app_users SET status='withdrawn' WHERE id=%s", (carer,))

            cur.execute(
                "SELECT actor_app_user_id FROM care_events WHERE id=%s", (event,)
            )
            row = cur.fetchone()
            assert row is not None, "케어 기록 행까지 지워졌다 — 사실은 강아지의 것이다"
            assert row[0] is None, "탈퇴한 돌보미의 id 가 남의 집 케어 로그에 남았다"
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
    넣는다)로 하면 같은 삽입이 통과해야 한다.

    ⚠️ **이 테스트는 `transfer_owner` 를 안 부른다.** 여기 있는 것은 생 SQL 뿐이라
    "트리거가 순서에 민감하다" 는 사실만 재고, 서비스가 그 순서를 실제로 따르는지는
    아래 `test_transfer_owner_survives_the_trigger` 가 진짜 서비스를 불러서 본다 —
    SQLAlchemy 의 autoflush 가 `pets` UPDATE 를 `pet_members` INSERT 보다 먼저 내보내는
    것에 기대고 있는데, 그것을 재는 자리가 거기다.
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


def test_invite_receipts_migration_is_rerunnable():
    """`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 두 번 — 버전 테이블이 없어 매번 다시
    돌 수 있어야 한다(CLAUDE.md).
    """
    conn = _postgres_or_skip()
    try:
        sql = open(
            "../db/migrations/2026-09-10_pet_invite_receipts.sql", encoding="utf-8"
        ).read()
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute(sql)
    finally:
        conn.rollback()
        conn.close()


def test_accepted_by_fk_is_set_null_not_cascade():
    """`pet_invites.accepted_by` 의 FK 삭제 동작은 **SET NULL** 이다 — CASCADE 가 아니다.

    영수증(그 사람이 그날 받았다는 사실)은 그 사람의 행이 훗날 진짜로 지워지는 날에도
    남아야 한다(`care_events.actor_app_user_id` 와 같은 이유, `models/pet_invite.py`).
    가짜 리포지토리는 FK 의 삭제 동작을 아예 흉내 내지 않으므로 이것은 진짜 DB 에서만
    증명된다. 탈퇴(`withdraw()`)는 `app_users` 행을 안 지우므로(§1 "함정") 이 경로가
    실제로 도는 유일한 자리는 그 행이 언젠가 진짜로 지워지는 날인데, 그 날을 여기서
    앞당겨 확인한다.
    """
    conn = _postgres_or_skip()
    try:
        with conn.cursor() as cur:
            owner, carer, pet = _seed(cur)
            invite = uuid.uuid4()
            cur.execute(
                "INSERT INTO pet_invites"
                " (id, pet_id, invited_by, token_hash, expires_at, accepted_at, accepted_by)"
                " VALUES (%s, %s, %s, %s, NOW() + INTERVAL '1 day', NOW(), %s)",
                (invite, pet, owner, "c" * 64, carer),
            )

            cur.execute("DELETE FROM app_users WHERE id=%s", (carer,))

            cur.execute("SELECT accepted_by FROM pet_invites WHERE id=%s", (invite,))
            row = cur.fetchone()
            assert row is not None, "영수증 행이 CASCADE 로 같이 지워졌다 — SET NULL 이어야 한다"
            assert row[0] is None, "accepted_by 가 안 비워졌다"
    finally:
        conn.rollback()
        conn.close()


def _sqlalchemy_dsn_or_skip() -> str:
    """같은 loopback 가드를 지난 뒤 **SQLAlchemy(asyncpg) DSN** 을 돌려준다.

    아래 삭제 자격 증명만 이것을 쓴다. 그 판정은 psycopg 로 **흉내 내면 의미가 없다** —
    테스트가 직접 쓴 SQL 을 테스트가 확인하는 꼴이 된다. 진짜 `care_repo.get_deletable`
    을 진짜 DB 에서 불러야, 조건을 `pet_repo.member_condition` 로 잘못 바꿔 놓은 구현이 여기서
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
    셋째가 이 테스트의 이유다 — 구성원 전체(`member_condition`)로 열어 놓아도 나머지 셋은 전부
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


async def test_care_event_delete_needs_current_membership():
    """적은 사람의 삭제 자격은 **지금도 그 행의 구성원일 때만**이다 (#574).

    나가기·내보내기(`remove_member`)는 `pet_members` 행을 지우고 기록은 그대로 둔다. 그 뒤에
    `actor_app_user_id` 만 보는 조건이면 읽지도 못하는 줄을 id 로 지운다 — 가짜 대역은 조건을
    흉내 낸 것이라 여기서 **진짜 SQL**(`member_condition` 의 서브쿼리)이 도는지 본다.

    세 갈래: 적은 사람 + 구성원 ✅ · 같은 사람 멤버십 행을 지운 뒤 ❌ · 그 행의 대표 ✅.
    """
    dsn = _sqlalchemy_dsn_or_skip()

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from daengs_backend.repositories import care_event as care_repo

    engine = create_async_engine(dsn)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        owner, carer = (str(uuid.uuid4()) for _ in range(2))
        pet, event = str(uuid.uuid4()), str(uuid.uuid4())

        for uid in (owner, carer):
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
        await session.execute(
            text(
                "INSERT INTO pet_members (pet_id, app_user_id)"
                " VALUES (CAST(:p AS uuid), CAST(:u AS uuid))"
            ),
            {"p": pet, "u": carer},
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

        assert await deletable_by(carer) is not None, "구성원인 적은 사람이 자기 기록을 못 지운다"

        # 나가기가 하는 일과 같다 — 멤버십 행만 지우고 기록은 둔다.
        await session.execute(
            text(
                "DELETE FROM pet_members"
                " WHERE pet_id = CAST(:p AS uuid) AND app_user_id = CAST(:u AS uuid)"
            ),
            {"p": pet, "u": carer},
        )

        assert await deletable_by(carer) is None, "나간 사람이 그 행의 기록을 지울 수 있다"
        found = await deletable_by(owner)
        assert found is not None, "적은 사람이 나간 뒤 대표가 그 기록을 못 지운다"
        assert str(found.actor_app_user_id) == carer, "기록의 actor 가 바뀌었다"
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


async def test_transfer_owner_survives_the_trigger():
    """**진짜 `transfer_owner` 를 진짜 DB 에서** 부른다 — 이것 하나가 두 구멍을 덮는다.

    ① `pet_members_not_owner` 트리거가 승계를 거절하지 않는가. 위
       `test_succession_order_matters` 는 생 SQL 이라 "트리거가 순서에 민감하다" 까지만
       재고, 서비스가 그 순서를 지키는지는 아무것도 안 본다.
    ② `transfer_owner` 는 오늘 **SQLAlchemy autoflush 가 `pets` UPDATE 를 `pet_members`
       INSERT 보다 먼저 내보내기 때문에** 맞다. 그 순서는 코드 어디에도 안 적혀 있고
       가짜 리포지토리에는 트리거가 없어 뒤집혀도 조용히 통과한다. 여기서만 걸린다.

    바깥 트랜잭션에 savepoint 로 얹어(`join_transaction_mode="create_savepoint"`)
    서비스의 `commit()` 을 그 안에 가두고, 끝에서 통째로 rollback 한다 — 이 파일의
    "모든 쓰기를 한 트랜잭션에서 검사하고 rollback" 규칙 그대로다.
    """
    dsn = _sqlalchemy_dsn_or_skip()

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from daengs_backend.services import pet_member as member_service

    engine = create_async_engine(dsn)
    conn = await engine.connect()
    outer = await conn.begin()
    session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint")
    try:
        owner, carer = (uuid.uuid4() for _ in range(2))
        pet = uuid.uuid4()
        for uid in (owner, carer):
            await session.execute(
                text(
                    "INSERT INTO app_users (id, kakao_id, status)"
                    " VALUES (CAST(:i AS uuid), :k, 'active')"
                ),
                {"i": str(uid), "k": uuid.uuid4().int % 10**12},
            )
        await session.execute(
            text(
                "INSERT INTO pets (id, app_user_id, name, breed)"
                " VALUES (CAST(:p AS uuid), CAST(:o AS uuid), '맥스', '믹스')"
            ),
            {"p": str(pet), "o": str(owner)},
        )
        await session.execute(
            text(
                "INSERT INTO pet_members (pet_id, app_user_id)"
                " VALUES (CAST(:p AS uuid), CAST(:u AS uuid))"
            ),
            {"p": str(pet), "u": str(carer)},
        )
        # 옛 대표가 뿌린 초대도 하나 — 승계가 그것을 죽이는지까지 본다.
        await session.execute(
            text(
                "INSERT INTO pet_invites (pet_id, invited_by, token_hash, expires_at)"
                " VALUES (CAST(:p AS uuid), CAST(:o AS uuid), :h,"
                "         NOW() + INTERVAL '1 day')"
            ),
            {"p": str(pet), "o": str(owner), "h": "b" * 64},
        )
        await session.flush()

        await member_service.transfer_owner(session, owner, pet, carer)

        new_owner = await session.scalar(
            text("SELECT app_user_id FROM pets WHERE id = CAST(:p AS uuid)"),
            {"p": str(pet)},
        )
        assert new_owner == carer, "대표가 안 바뀌었다"
        carers = list(
            await session.scalars(
                text(
                    "SELECT app_user_id FROM pet_members"
                    " WHERE pet_id = CAST(:p AS uuid)"
                ),
                {"p": str(pet)},
            )
        )
        assert carers == [owner], f"옛 대표만 돌보미로 남아야 한다 (지금 {carers})"
        left = await session.scalar(
            text(
                "SELECT count(*) FROM pet_invites WHERE pet_id = CAST(:p AS uuid)"
            ),
            {"p": str(pet)},
        )
        assert left == 0, "옛 대표가 뿌린 초대가 살아남았다"
    finally:
        await session.close()
        await outer.rollback()
        await conn.close()
        await engine.dispose()


async def _seed_screening_fixture(session):
    """스크리닝 진짜-DB 테스트 둘이 같이 쓰는 세팅.

    강아지 둘(A·B), A 의 대표(owner)·창작자 겸 돌보미(carer)·A 의 **다른** 돌보미
    (other_carer), B 의 돌보미(cross_member — A 에는 안 걸림), 아무 데도 안 걸린
    stranger. 기록 둘 — A 에 붙은 것(rec_pet, carer 가 만듦)과 개인 기록
    (rec_personal, 역시 carer 가 만듦, `pet_id IS NULL`).
    """
    from sqlalchemy import text

    ids = {
        name: uuid.uuid4()
        for name in ("owner", "carer", "other_carer", "cross_owner", "cross_member", "stranger")
    }
    pet_a, pet_b = uuid.uuid4(), uuid.uuid4()
    rec_pet, rec_personal = uuid.uuid4(), uuid.uuid4()

    for uid in ids.values():
        await session.execute(
            text(
                "INSERT INTO app_users (id, kakao_id, status)"
                " VALUES (CAST(:i AS uuid), :k, 'active')"
            ),
            {"i": str(uid), "k": uuid.uuid4().int % 10**12},
        )
    await session.execute(
        text(
            "INSERT INTO pets (id, app_user_id, name, breed)"
            " VALUES (CAST(:p AS uuid), CAST(:o AS uuid), '맥스', '믹스'),"
            "        (CAST(:p2 AS uuid), CAST(:o2 AS uuid), '두찌', '믹스')"
        ),
        {"p": str(pet_a), "o": str(ids["owner"]), "p2": str(pet_b), "o2": str(ids["cross_owner"])},
    )
    for pid, uid in (
        (pet_a, ids["carer"]),
        (pet_a, ids["other_carer"]),
        (pet_b, ids["cross_member"]),
    ):
        await session.execute(
            text(
                "INSERT INTO pet_members (pet_id, app_user_id)"
                " VALUES (CAST(:p AS uuid), CAST(:u AS uuid))"
            ),
            {"p": str(pid), "u": str(uid)},
        )
    for rid, pid in ((rec_pet, str(pet_a)), (rec_personal, None)):
        await session.execute(
            text(
                "INSERT INTO screening_records"
                " (id, app_user_id, pet_id, photo_storage_key, photo_content_type)"
                " VALUES (CAST(:r AS uuid), CAST(:a AS uuid), CAST(:p AS uuid),"
                "         :k, 'image/jpeg')"
            ),
            {"r": str(rid), "a": str(ids["carer"]), "p": pid, "k": f"screening/{rid}.jpg"},
        )
    await session.flush()
    return ids, rec_pet, rec_personal


async def test_screening_accessible_respects_dog_membership_and_personal_privacy():
    """`screening_repo.get_accessible`/`list_accessible` — 창작자 ∪ **그 아이의 구성원**만,
    개인 기록(`pet_id IS NULL`)은 **창작자만** (docs/co-care.md §2, Task 14).

    가짜 리포지토리는 이 쿼리를 통째로 갈아치우므로, 개인 기록이 `pet_id IN (...)` 서브쿼리
    바깥에 있다는 사실 — 이 기능 전체가 기대는 구조적 성질 — 은 진짜 DB 로만 증명된다.
    """
    dsn = _sqlalchemy_dsn_or_skip()

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from daengs_backend.repositories import screening as screening_repo

    engine = create_async_engine(dsn)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        ids, rec_pet, rec_personal = await _seed_screening_fixture(session)

        async def sees(who: str, record_id: uuid.UUID) -> bool:
            return (
                await screening_repo.get_accessible(session, ids[who], record_id)
                is not None
            )

        # 강아지에 붙은 기록 — 창작자와 **그 아이의 구성원**(대표 포함) 전원이 본다.
        assert await sees("carer", rec_pet), "창작자가 자기 기록을 못 본다"
        assert await sees("owner", rec_pet), "대표가 돌보미가 만든 기록을 못 본다"
        assert await sees("other_carer", rec_pet), "같은 아이의 다른 돌보미가 못 본다"
        # 다른 강아지의 구성원과 남남은 못 본다.
        assert not await sees("cross_member", rec_pet), "남의 강아지 돌보미가 봤다"
        assert not await sees("stranger", rec_pet), "남남이 봤다"

        # 개인 기록 — **창작자만.** 같은 강아지를 함께 돌보는 사이여도 못 본다.
        assert await sees("carer", rec_personal), "창작자가 자기 개인 기록을 못 본다"
        assert not await sees("owner", rec_personal), "대표가 돌보미의 개인 기록을 봤다"
        assert not await sees("other_carer", rec_personal), "다른 돌보미가 개인 기록을 봤다"

        # list_accessible 도 같은 판정이어야 한다 — 대표의 목록엔 rec_pet 만, 창작자의
        # 목록엔 둘 다.
        owner_ids = {
            r.id for r in await screening_repo.list_accessible(session, ids["owner"])
        }
        assert owner_ids == {rec_pet}, f"대표 목록이 개인 기록을 흘렸다: {owner_ids}"
        carer_ids = {
            r.id for r in await screening_repo.list_accessible(session, ids["carer"])
        }
        assert carer_ids == {rec_pet, rec_personal}, f"창작자 목록이 모자랐다: {carer_ids}"
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


async def test_screening_deletable_is_creator_or_dog_owner():
    """`screening_repo.get_deletable` — **창작자 또는 그 아이의 대표**만 (`care_repo.
    get_deletable` 과 같은 모양). 구성원 전체로 잘못 열면 `other_carer` 가 통과해 여기서 잡힌다.

    `for_update=True` 로도 한 번 불러 — LEFT OUTER JOIN 위의 `with_for_update(of=
    ScreeningRecord)` 가 nullable 쪽(Pet)이 아니라 non-nullable 쪽(ScreeningRecord)을
    잠그기 때문에 합법이라는 것을 **진짜 DB 에서** 확인한다. 가짜로는 이 SQL 자체가
    안 돈다.
    """
    dsn = _sqlalchemy_dsn_or_skip()

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from daengs_backend.repositories import screening as screening_repo

    engine = create_async_engine(dsn)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        ids, rec_pet, _rec_personal = await _seed_screening_fixture(session)

        async def deletable_by(who: str, *, for_update: bool = False):
            return await screening_repo.get_deletable(
                session, ids[who], rec_pet, for_update=for_update
            )

        assert await deletable_by("carer") is not None, "창작자가 자기 기록을 못 지운다"
        assert await deletable_by("owner") is not None, "대표가 돌보미의 기록을 못 지운다"
        assert await deletable_by("other_carer") is None, "다른 돌보미가 남의 기록을 지울 수 있다"
        assert await deletable_by("stranger") is None, "남남이 지울 수 있다"

        # 잠금 경로 — LEFT JOIN 위의 FOR UPDATE OF 가 실제로 실행된다.
        locked = await deletable_by("owner", for_update=True)
        assert locked is not None, "for_update 경로에서 대표가 못 찾는다"
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


async def test_record_profile_sees_other_members_walks():
    """산책 기록 프로필은 **그 아이의 산책 전부**를 본다 — 부른 사람 것만이 아니다.

    게이트(`services/walk_entry.py::profile`)가 구성원에게 열린 뒤에도 이 질의가
    `Walk.app_user_id` 로 거르면 돌보미는 **200 인데 내용이 빈** 응답을 받는다 — 예전에는
    404 였으므로 "권한이 없다" 가 "기록이 없다" 로 조용히 바뀌는 셈이다. `walk.py` 의
    `count_for_pet_between` 에서 소유자 조건을 뺀 것과 같은 자리다.

    가짜 대역은 이 질의를 통째로 monkeypatch 하므로(`test_walk_entry_http.py`)
    **진짜 DB 에서만** 증명된다.
    """
    dsn = _sqlalchemy_dsn_or_skip()

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from daengs_backend.repositories import walk_entry as walk_entry_repo
    from daengs_backend.schemas.walk_entry import RecordProfileQuery

    engine = create_async_engine(dsn)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        owner, carer = (str(uuid.uuid4()) for _ in range(2))
        pet, walk = str(uuid.uuid4()), str(uuid.uuid4())
        for uid in (owner, carer):
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
        await session.execute(
            text(
                "INSERT INTO pet_members (pet_id, app_user_id)"
                " VALUES (CAST(:p AS uuid), CAST(:u AS uuid))"
            ),
            {"p": pet, "u": carer},
        )
        # 산책은 **대표가** 올렸고 그 아이가 동행했다.
        await session.execute(
            text(
                "INSERT INTO walks (id, app_user_id, client_session_id, started_at, ended_at)"
                " VALUES (CAST(:w AS uuid), CAST(:o AS uuid), CAST(:c AS uuid),"
                "         NOW() - INTERVAL '1 hour', NOW())"
            ),
            {"w": walk, "o": owner, "c": str(uuid.uuid4())},
        )
        await session.execute(
            text(
                "INSERT INTO walk_pets (walk_id, pet_id)"
                " VALUES (CAST(:w AS uuid), CAST(:p AS uuid))"
            ),
            {"w": walk, "p": pet},
        )

        spec = RecordProfileQuery(pet_id=uuid.UUID(pet))
        found = await walk_entry_repo.profile_walks(session, uuid.UUID(carer), spec)
        assert [str(w.id) for w in found] == [walk], (
            "돌보미의 프로필에서 대표의 산책이 빠졌다 — 200 인데 빈 응답이 된다"
        )
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


# ── 나가기의 트랜잭션 경계 ──────────────────────────────────────────────────
#
# 멤버십 제거와 논리 연결 해제는 **둘이 갈라지는 순간이 있으면 안 된다** (docs/co-care.md
# 「연결 수명주기」). 멤버십만 지워지고 연결이 남으면 나간 사람의 pet 행이 그룹에 그대로
# 있어 **공동 조회로 남의 집 케어·산책을 계속 읽는다.** 가짜 저장소에는 트랜잭션이 없어
# 이 성질은 여기서만 증명된다.


async def _seed_linked_group(session, *, primary_to_anchor: bool = False):
    """수락 직후의 모양 그대로. A 의 앵커 행 · B 의 연결된 행 · B 의 앵커 행 돌보미 자리.

    `primary_to_anchor` 는 B 의 **대표 강아지를 앵커 행으로** 세워 둔다. 나가면 못 보게
    되는 행이라 `remove_member` 가 수선해야 하는 자리다 — 수선이 같은 트랜잭션에 있는지를
    보려면 수선할 것이 실제로 있어야 한다.
    """
    from sqlalchemy import text

    a_user, b_user = uuid.uuid4(), uuid.uuid4()
    a_pet, b_pet, identity = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    for uid in (a_user, b_user):
        await session.execute(
            text(
                "INSERT INTO app_users (id, kakao_id, status)"
                " VALUES (CAST(:i AS uuid), :k, 'active')"
            ),
            {"i": str(uid), "k": uuid.uuid4().int % 10**12},
        )
    await session.execute(
        text(
            "INSERT INTO pets (id, app_user_id, name, breed)"
            " VALUES (CAST(:a AS uuid), CAST(:au AS uuid), '롱이씨', '믹스'),"
            "        (CAST(:b AS uuid), CAST(:bu AS uuid), '롱롱씨', '믹스')"
        ),
        {"a": str(a_pet), "au": str(a_user), "b": str(b_pet), "bu": str(b_user)},
    )
    await session.execute(
        text(
            "INSERT INTO pet_identities (id, owner_pet_id)"
            " VALUES (CAST(:i AS uuid), CAST(:a AS uuid))"
        ),
        {"i": str(identity), "a": str(a_pet)},
    )
    await session.execute(
        text(
            "UPDATE pets SET identity_id = CAST(:i AS uuid)"
            " WHERE id IN (CAST(:a AS uuid), CAST(:b AS uuid))"
        ),
        {"i": str(identity), "a": str(a_pet), "b": str(b_pet)},
    )
    await session.execute(
        text(
            "INSERT INTO pet_members (pet_id, app_user_id)"
            " VALUES (CAST(:a AS uuid), CAST(:u AS uuid))"
        ),
        {"a": str(a_pet), "u": str(b_user)},
    )
    if primary_to_anchor:
        await session.execute(
            text(
                "UPDATE app_users SET primary_pet_id = CAST(:a AS uuid)"
                " WHERE id = CAST(:u AS uuid)"
            ),
            {"a": str(a_pet), "u": str(b_user)},
        )
    await session.flush()
    return {
        "a_user": a_user, "b_user": b_user,
        "a_pet": a_pet, "b_pet": b_pet, "identity": identity,
    }


async def _primary_of(session, user_id):
    from sqlalchemy import text

    return await session.scalar(
        text("SELECT primary_pet_id FROM app_users WHERE id = CAST(:u AS uuid)"),
        {"u": str(user_id)},
    )


async def _leave_state(session, ids) -> tuple[int, int, int]:
    """(B 의 돌보미 행 수, 아직 연결된 pet 행 수, 그룹 행 수). 셋이 **같이** 움직여야 한다."""
    from sqlalchemy import text

    members = await session.scalar(
        text(
            "SELECT count(*) FROM pet_members WHERE app_user_id = CAST(:u AS uuid)"
        ),
        {"u": str(ids["b_user"])},
    )
    linked = await session.scalar(
        text(
            "SELECT count(*) FROM pets WHERE identity_id = CAST(:i AS uuid)"
        ),
        {"i": str(ids["identity"])},
    )
    groups = await session.scalar(
        text("SELECT count(*) FROM pet_identities WHERE id = CAST(:i AS uuid)"),
        {"i": str(ids["identity"])},
    )
    return int(members), int(linked), int(groups)


async def test_leave_commits_membership_and_identity_together():
    """**진짜 `remove_member` 를 진짜 DB 에서**, 그것도 나가는 사람의 **자기 카드 id** 로.

    연결한 공동 보호자에게 `display_pet_id` 는 자기가 대표인 행이라, 행 대표로 판단하던
    동안은 이 호출이 `CannotRemoveOwnerError` 로 막혔다 — 앵커 행 id 로만 통했고 앱은 그
    id 를 알 방법이 없었다. 여기서는 그 호출이 통하는 것과, 멤버십·연결·그룹 행이 **한
    번의 commit 으로 함께** 사라지는 것을 같이 본다.
    """
    dsn = _sqlalchemy_dsn_or_skip()

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from daengs_backend.services import pet_member as member_service

    engine = create_async_engine(dsn)
    conn = await engine.connect()
    outer = await conn.begin()
    session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint")
    try:
        ids = await _seed_linked_group(session)
        assert await _leave_state(session, ids) == (1, 2, 1), "씨앗이 안 섰다"

        # ⚠️ `pet_id` 는 **B 의 행**이다. 앵커(A 의 행)가 아니다.
        await member_service.remove_member(
            session, ids["b_user"], ids["b_pet"], ids["b_user"]
        )

        assert await _leave_state(session, ids) == (0, 0, 0), (
            "멤버십·연결·그룹 행 중 남은 것이 있다 — 하나라도 남으면 나간 사람이"
            " 공동 조회로 계속 읽는다"
        )
        # 행도 기록도 안 지워진다 — 나가기는 관계만 끊는다.
        from sqlalchemy import text

        rows = await session.scalar(
            text(
                "SELECT count(*) FROM pets"
                " WHERE id IN (CAST(:a AS uuid), CAST(:b AS uuid))"
            ),
            {"a": str(ids["a_pet"]), "b": str(ids["b_pet"])},
        )
        assert int(rows) == 2, "나가기가 pet 행을 지웠다"
    finally:
        await session.close()
        await outer.rollback()
        await conn.close()
        await engine.dispose()


async def test_leave_rolls_back_everything_when_a_later_step_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    """마지막 단계가 터지면 **앞의 것도 안 일어난 것이 된다.**

    `remove_member` 는 commit 을 끝에 한 번만 한다. 중간에 commit 이 하나라도 끼면
    "멤버십은 지워졌는데 연결은 남은" 상태가 **DB 에 남아** 나간 사람이 계속 읽는다.
    여기서는 대표 강아지 수선 직전 단계를 일부러 터뜨려 그 경계를 잰다 — 가짜 저장소에는
    트랜잭션이 없어 이 회귀는 여기서만 걸린다.
    """
    dsn = _sqlalchemy_dsn_or_skip()

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from daengs_backend.repositories import app_user as app_user_repo
    from daengs_backend.services import pet_member as member_service

    engine = create_async_engine(dsn)
    conn = await engine.connect()
    outer = await conn.begin()
    session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint")
    try:
        ids = await _seed_linked_group(session)
        # 씨앗을 바깥 트랜잭션에 올려 둔다 — 아래 rollback 이 씨앗까지 지우면 안 된다.
        await session.commit()
        assert await _leave_state(session, ids) == (1, 2, 1)

        async def boom(*_args, **_kw):
            raise RuntimeError("일부러 터뜨림")

        monkeypatch.setattr(app_user_repo, "get_by_id", boom)

        with pytest.raises(RuntimeError):
            await member_service.remove_member(
                session, ids["b_user"], ids["b_pet"], ids["b_user"]
            )
        # 요청 세션이 닫히며 도는 것과 같다 (`core/database.py::get_session`).
        await session.rollback()

        assert await _leave_state(session, ids) == (1, 2, 1), (
            "중간에 터졌는데 멤버십이나 연결 중 일부가 이미 적용돼 있다"
        )
    finally:
        await session.close()
        await outer.rollback()
        await conn.close()
        await engine.dispose()


async def test_leave_repairs_primary_pet_in_the_same_commit():
    """나가기의 **세 번째 조각** — 멤버십·연결과 함께 대표 강아지 수선도 한 commit 안이다.

    앞의 두 테스트는 수선할 것이 없는 씨앗을 썼다. 여기서는 B 의 대표 강아지를 **앵커 행**
    으로 세워 둔다 — 나가면 못 보게 되는 행이라, 안 고치면 첫 화면이 자기가 접근할 수 없는
    아이를 가리킨 채 남는다. 고친 결과가 **자기 행**이어야 한다는 것까지 본다 (비우면 안
    된다 — 그 사람에게는 계속 볼 수 있는 아이가 있다).
    """
    dsn = _sqlalchemy_dsn_or_skip()

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from daengs_backend.services import pet_member as member_service

    engine = create_async_engine(dsn)
    conn = await engine.connect()
    outer = await conn.begin()
    session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint")
    try:
        ids = await _seed_linked_group(session, primary_to_anchor=True)
        assert await _leave_state(session, ids) == (1, 2, 1), "씨앗이 안 섰다"
        assert await _primary_of(session, ids["b_user"]) == ids["a_pet"]

        await member_service.remove_member(
            session, ids["b_user"], ids["b_pet"], ids["b_user"]
        )

        assert await _leave_state(session, ids) == (0, 0, 0)
        assert await _primary_of(session, ids["b_user"]) == ids["b_pet"], (
            "대표 강아지가 못 보게 된 앵커 행을 계속 가리킨다"
        )
        # A 의 대표는 안 건드린다 — 남의 첫 화면을 흔들 이유가 없다.
        assert await _primary_of(session, ids["a_user"]) is None
    finally:
        await session.close()
        await outer.rollback()
        await conn.close()
        await engine.dispose()


async def test_leave_rolls_back_membership_detach_and_primary_repair_together(
    monkeypatch: pytest.MonkeyPatch,
):
    """**대표 수선을 해 놓은 직후에 터뜨린다** — 셋이 전부 안 일어난 것이 되어야 한다.

    위 `test_leave_rolls_back_everything_when_a_later_step_fails` 는 수선 **직전**을
    터뜨리므로 수선 자체가 롤백되는지는 못 본다. 여기서는 `app_user_repo.get_by_id` 가
    돌려주는 행을 **대입 순간 터지는 껍데기**로 감싸, 진짜 ORM 객체에는 수선이 적용된
    채로 예외가 나게 한다. 그래야 롤백이 되돌려야 할 것이 셋 다 있는 상태가 된다.

    `session.commit` 을 터뜨리는 방법은 안 쓴다 — 그러면 중간에 끼어든 commit 까지 같이
    막혀서, 정작 잡아야 할 "중간 commit" 회귀가 통과해 버린다.
    """
    dsn = _sqlalchemy_dsn_or_skip()

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from daengs_backend.repositories import app_user as app_user_repo
    from daengs_backend.services import pet_member as member_service

    real_get_by_id = app_user_repo.get_by_id

    class _ExplodeOnRepair:
        """읽기는 그대로 넘기고, `primary_pet_id` **대입이 끝난 뒤** 터진다."""

        def __init__(self, inner) -> None:
            object.__setattr__(self, "_inner", inner)

        def __getattr__(self, name):
            return getattr(object.__getattribute__(self, "_inner"), name)

        def __setattr__(self, name, value) -> None:
            setattr(object.__getattribute__(self, "_inner"), name, value)
            if name == "primary_pet_id":
                raise RuntimeError("대표 수선 직후 일부러 터뜨림")

    async def wrapped(session, app_user_id):
        row = await real_get_by_id(session, app_user_id)
        return None if row is None else _ExplodeOnRepair(row)

    engine = create_async_engine(dsn)
    conn = await engine.connect()
    outer = await conn.begin()
    session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint")
    try:
        ids = await _seed_linked_group(session, primary_to_anchor=True)
        # 씨앗을 바깥 트랜잭션에 올려 둔다 — 아래 rollback 이 씨앗까지 지우면 안 된다.
        await session.commit()
        assert await _leave_state(session, ids) == (1, 2, 1)
        assert await _primary_of(session, ids["b_user"]) == ids["a_pet"]

        monkeypatch.setattr(app_user_repo, "get_by_id", wrapped)

        with pytest.raises(RuntimeError):
            await member_service.remove_member(
                session, ids["b_user"], ids["b_pet"], ids["b_user"]
            )
        # 요청 세션이 닫히며 도는 것과 같다 (`core/database.py::get_session`).
        await session.rollback()

        assert await _leave_state(session, ids) == (1, 2, 1), (
            "멤버십 삭제나 연결 해제 중 일부가 이미 DB 에 남았다 — 중간에 commit 이 끼었다"
        )
        assert await _primary_of(session, ids["b_user"]) == ids["a_pet"], (
            "대표 수선만 살아남았다 — 셋이 한 덩어리가 아니다"
        )
    finally:
        await session.close()
        await outer.rollback()
        await conn.close()
        await engine.dispose()
