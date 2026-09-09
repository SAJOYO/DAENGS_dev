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


def _postgres_or_skip():
    dsn = os.environ.get(
        "DAENGS_TEST_DATABASE_URL",
        "postgresql://postgres:postgres@127.0.0.1:5432/vectordb",
    )
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
