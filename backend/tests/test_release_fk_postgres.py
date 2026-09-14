"""실제 PostgreSQL 이 release-critical FK CASCADE 를 수행한다는 증명.

기본값은 loopback 개발 DB 뿐입니다. 공유/운영 DB 를 우연히 만지지 않으며, 모든 쓰기는
한 트랜잭션에서 검사한 뒤 rollback 합니다. 다른 로컬 테스트 DB 는
``DAENGS_TEST_DATABASE_URL`` 로 명시할 수 있습니다.
"""

from __future__ import annotations

import os
import uuid
from urllib.parse import urlparse

import psycopg
import pytest

#: First connection failure's skip reason (#519) — don't wait 3 seconds again per test.
_unreachable: str | None = None


def _postgres_or_skip():
    global _unreachable
    dsn = os.environ.get(
        "DAENGS_TEST_DATABASE_URL",
        "postgresql://postgres:postgres@127.0.0.1:5432/vectordb",
    )
    parsed = urlparse(dsn)
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        pytest.fail("FK proof refuses a non-loopback PostgreSQL database")
    if _unreachable is not None:
        pytest.skip(_unreachable)
    try:
        conn = psycopg.connect(dsn, connect_timeout=3)
    except psycopg.Error as exc:
        _unreachable = f"local PostgreSQL unavailable: {type(exc).__name__}"
        pytest.skip(_unreachable)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT to_regclass('public.app_users'), to_regclass('public.gait_records'), "
            "to_regclass('public.walk_point_chunks')"
        )
        if any(value is None for value in cur.fetchone()):
            conn.close()
            pytest.skip("local PostgreSQL does not have the repository init schema")
    return conn


def test_real_postgres_pet_and_walk_cascades_preserve_no_children() -> None:
    conn = _postgres_or_skip()
    owner_id = uuid.uuid4()
    pet_id = uuid.uuid4()
    gait_id = uuid.uuid4()
    walk_id = uuid.uuid4()
    kakao_id = uuid.uuid4().int % 9_000_000_000_000_000_000

    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO app_users (id, kakao_id) VALUES (%s, %s)",
                (owner_id, kakao_id),
            )
            cur.execute(
                "INSERT INTO pets (id, app_user_id, name, breed) "
                "VALUES (%s, %s, 'FK proof', 'mix')",
                (pet_id, owner_id),
            )
            cur.execute(
                "INSERT INTO gait_records "
                "(id, pet_id, status, original_storage_key, overlay_storage_key) "
                "VALUES (%s, %s, 'DONE', 'proof/original', 'proof/overlay')",
                (gait_id, pet_id),
            )
            cur.execute(
                "INSERT INTO walks "
                "(id, app_user_id, client_session_id, started_at, ended_at) "
                "VALUES (%s, %s, %s, NOW(), NOW())",
                (walk_id, owner_id, uuid.uuid4()),
            )
            cur.execute(
                "INSERT INTO walk_point_chunks "
                "(walk_id, seq_from, seq_to, point_count, payload) "
                "VALUES (%s, 0, 0, 1, %s::jsonb)",
                (walk_id, '{"v":1,"pts":[[0,0,0,37.5,127.0,null,0]]}'),
            )

            cur.execute("DELETE FROM pets WHERE id = %s", (pet_id,))
            cur.execute("SELECT count(*) FROM gait_records WHERE id = %s", (gait_id,))
            assert cur.fetchone()[0] == 0, "pets -> gait_records must be ON DELETE CASCADE"

            cur.execute("DELETE FROM walks WHERE id = %s", (walk_id,))
            cur.execute(
                "SELECT count(*) FROM walk_point_chunks WHERE walk_id = %s",
                (walk_id,),
            )
            assert cur.fetchone()[0] == 0, "walks -> coordinate chunks must cascade"
    finally:
        conn.rollback()
        conn.close()
