"""적재 키 회귀 — 한 청킹의 두 임베딩이 공존해야 한다 (#329).

2026-09-08 이전에는 `chunk_id` 가 PK 이고 적재가 `ON CONFLICT(chunk_id)` 여서, 다른 모델로
재적재하면 **옛 벡터를 덮어썼다.** 예외도 경고도 안 났다. 이 테스트가 그 조용한 실패를
소리 나게 만든다.

⚠ **실 DB 를 안 건드린다.** 임시 스키마를 만들어 그 안에서만 돌고 끝나면 지운다. DB 가 없는
개발 PC 에서는 통째로 skip 한다 — 스키마 제약을 재는 테스트라 가짜로는 대신할 수 없다.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg", reason="적재 키 검증에는 psycopg 가 필요합니다")

SCHEMA_SQL = Path(__file__).resolve().parents[2] / "db" / "init" / "05_training_rag.sql"

#: 첫 연결 실패의 skip 사유 (#519). 같은 실행 안에서는 다시 연결을 시도하지 않는다.
_unreachable: str | None = None


def _dsn() -> str:
    """`RAG_PGVECTOR_DSN` 이 있으면 그것, 없으면 앱 설정에서 조립한다."""
    dsn = os.getenv("RAG_PGVECTOR_DSN")
    if dsn:
        return dsn
    from daengs_backend.config import settings

    return (
        f"postgresql://{settings.db_user}:{settings.db_password.get_secret_value()}"
        f"@{settings.db_host}:{settings.db_port}/{settings.db_name}"
    )


@pytest.fixture
def schema():
    """세션 임시 테이블로 진짜 DDL 을 세운다. **`db/init/` 의 파일을 그대로 읽는다** —
    테스트가 스키마를 따로 적으면 둘이 어긋나도 아무도 모른다.

    ⚠ **`pg_temp` 를 쓰는 이유는 권한이다.** 앱 롤(`daengs`)에는 `CREATE SCHEMA` 권한이 없어
    임시 스키마를 못 만든다. `search_path` 를 `pg_temp` 로 두면 같은 DDL 이 임시 테이블을
    만들고, 세션이 끝나면 사라진다 — **실 DB 의 `training_rag_chunks` 는 안 건드린다.**
    `public` 을 뒤에 붙이는 것은 `vector` 타입과 확장이 거기 있기 때문이다.

    **연결 실패만 기억한다** (#519) — 못 붙는 PC 에서 테스트마다 10초씩 기다렸다. 붙은 뒤의
    DDL·권한 실패는 사유가 여러 가지라 매번 다시 판단한다.
    """
    global _unreachable
    if _unreachable is not None:
        pytest.skip(_unreachable)
    try:
        conn = psycopg.connect(_dsn(), connect_timeout=5)
    except psycopg.OperationalError as exc:
        _unreachable = f"임시 스키마를 못 세웠습니다 ({type(exc).__name__}) — 실 DB 가 필요합니다"
        pytest.skip(_unreachable)
    try:
        with conn.cursor() as cur:
            cur.execute("set search_path to pg_temp, public")
            cur.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
        conn.commit()
    except Exception as exc:  # noqa: BLE001 — 권한 부족 등 사유가 여러 가지다
        conn.close()
        pytest.skip(f"임시 스키마를 못 세웠습니다 ({type(exc).__name__}) — 실 DB 가 필요합니다")

    try:
        yield conn
    finally:
        conn.close()   # 임시 테이블은 세션이 끝나면 저절로 사라진다


UPSERT = """
INSERT INTO training_rag_chunks
  (chunk_id, document_id, chunk_index, text, token_count, metadata,
   embedding_model, embedding, content_sha256)
VALUES (%s, %s, %s, %s, %s, '{}'::jsonb, %s, %s::vector, %s)
ON CONFLICT (document_id, chunk_index, embedding_model) DO UPDATE SET
  chunk_id=EXCLUDED.chunk_id, text=EXCLUDED.text, token_count=EXCLUDED.token_count,
  metadata=EXCLUDED.metadata, embedding=EXCLUDED.embedding,
  content_sha256=EXCLUDED.content_sha256
RETURNING (xmax = 0) AS is_insert
"""


def _seed(cur, model: str, first: float) -> bool:
    """청크 하나를 넣는다. 반환값은 «새 행인가»."""
    cur.execute("""INSERT INTO training_rag_documents(document_id, source_id, content_sha256)
                   VALUES ('d1','d1','sha') ON CONFLICT (document_id) DO NOTHING""")
    vector = "[" + ",".join([str(first)] + ["0"] * 767) + "]"
    cur.execute(UPSERT, ("c1", "d1", 0, "본문", 3, model, vector, "sha"))
    return bool(cur.fetchone()[0])


def test_두_모델이_공존한다(schema) -> None:
    """이 카드의 전부다. 고치기 전에는 여기서 models 가 1 이었다."""
    with schema.cursor() as cur:
        cur.execute("set search_path to pg_temp, public")
        assert _seed(cur, "model-a", 0.1) is True
        assert _seed(cur, "model-b", 0.9) is True   # 다른 모델 → 새 행이어야 한다

        cur.execute("select count(*), count(distinct embedding_model) from training_rag_chunks")
        rows, models = cur.fetchone()

    assert (rows, models) == (2, 2), "다른 모델이 옛 행을 덮어썼습니다 — 이 카드가 고친 버그입니다"


def test_같은_모델_재적재는_갱신이다(schema) -> None:
    """공존을 켜느라 중복이 쌓이면 안 된다. 같은 모델은 여전히 한 행이다."""
    with schema.cursor() as cur:
        cur.execute("set search_path to pg_temp, public")
        assert _seed(cur, "model-a", 0.1) is True
        assert _seed(cur, "model-a", 0.2) is False  # 같은 모델 → 갱신

        cur.execute("select count(*) from training_rag_chunks")
        assert cur.fetchone()[0] == 1


def test_모델_벡터가_서로를_안_지운다(schema) -> None:
    """행이 둘이어도 값이 섞였으면 소용없다. 각자의 벡터가 남아야 한다."""
    with schema.cursor() as cur:
        cur.execute("set search_path to pg_temp, public")
        _seed(cur, "model-a", 0.1)
        _seed(cur, "model-b", 0.9)

        cur.execute("""select embedding_model, (embedding::text like '[0.1,%')
                       from training_rag_chunks order by embedding_model""")
        got = dict(cur.fetchall())

    assert got == {"model-a": True, "model-b": False}


def test_한_모델_안에서_chunk_id_는_유일하다(schema) -> None:
    """검색이 늘 한 모델로 거르므로 결과 안에서 id 하나가 청크 하나를 가리켜야 한다.
    그 불변식을 스키마가 든다 — 코드 규약으로 두면 조용히 깨진다."""
    with schema.cursor() as cur:
        cur.execute("set search_path to pg_temp, public")
        _seed(cur, "model-a", 0.1)
        vector = "[" + ",".join(["0.5"] * 768) + "]"

        with pytest.raises(psycopg.errors.UniqueViolation):
            # 같은 모델 · 같은 chunk_id 인데 다른 청크번호 → 막혀야 한다
            cur.execute(UPSERT, ("c1", "d1", 1, "다른 본문", 3, "model-a", vector, "sha"))
    schema.rollback()   # 실패한 트랜잭션을 닫아야 픽스처 정리가 돈다


def test_chunk_id_가_모델_간에는_겹쳐도_된다(schema) -> None:
    """`chunk_id` 형식을 안 바꾼 것이 이 카드의 요점이다 — 서빙 응답과 평가 산출물의 id 가
    그대로여야 기존 판정 파일·리포트가 맞는다."""
    with schema.cursor() as cur:
        cur.execute("set search_path to pg_temp, public")
        _seed(cur, "model-a", 0.1)
        _seed(cur, "model-b", 0.9)

        cur.execute("select count(distinct chunk_id) from training_rag_chunks")
        assert cur.fetchone()[0] == 1, "두 모델이 같은 chunk_id 를 공유해야 합니다"
