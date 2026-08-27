"""7단계 적재 테스트 — 계약과 규칙을 본다 (RAG-008, RAG-025).

**두 층으로 나눈다.** `prepare()` 는 DB 없이 검사할 수 있고(그래서 `parse_doc` 처럼 쓰기를
분리해 뒀다), 연결이 필요한 것은 DB 가 없으면 skip 한다 — `data/` 가 미추적이듯(RAG-017)
컨테이너도 항상 떠 있지는 않다.

**여기서 지키는 것 중 가장 중요한 하나는 `metadata.embedding_model` 이 "실제로 쓴 모델"이라는
것이다.** 지금 적재는 판정 승자(`qwen3`)가 아니라 기준선(`bge-m3`)으로 도는데(RAG-024 `판정 이후`),
그 둘이 어긋나면 DB 안의 벡터가 무엇으로 만들어졌는지를 아무도 못 믿게 된다.
"""
from __future__ import annotations

import hashlib

import pytest

from daengs_life.rag.core import config
from daengs_life.rag.stages import embed, load

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _prepared_or_skip(key: str = "bge-m3"):
    if not embed.parquet_path(key).is_file():
        pytest.skip(f"{key}.parquet 이 없다 — `python -m rag embed` 먼저")
    return load.prepare(key)


# ---------------------------------------------------------------- 기본값 = 결정 (RAG-024 판정 이후)
def test_default_model_is_the_baseline_not_the_winner() -> None:
    """첫 관통은 기준선으로 간다. **판정을 뒤집는 게 아니라 판정과 다르게 운영하는 것**이다.

    이 기본값 한 줄이 곧 "교체가 싸다"의 장치다 — 9단계까지 닿으면 여기를(또는 `--model` 을)
    바꾸는 것으로 승자 적재가 끝나야 한다.
    """
    assert config.settings.embedding_model_key == "bge-m3"
    assert config.settings.embedding_model_key in embed.MODELS


# ---------------------------------------------------------------- prepare (DB 없이)
def test_content_hash_is_sha256_of_content() -> None:
    """`content_hash` 는 `content` 의 SHA-256 이다 — `documents` 의 자연키(RAG-008)."""
    p = _prepared_or_skip()
    row = p.rows[0]
    assert row["content_hash"] == hashlib.sha256(row["content"].encode()).hexdigest()
    assert len(row["content_hash"]) == 64


def test_rows_are_chunks_minus_merged() -> None:
    """1,407청크 → 중복을 합친 만큼 줄어든 행 수. 산수가 안 맞으면 조용히 사라진 것이 있다."""
    p = _prepared_or_skip()
    assert len(p.rows) + p.merged == len(embed.load_chunks())


def test_merged_chunk_ids_survive_in_metadata() -> None:
    """RAG-025 ③ — 합쳐진 주소가 **DB 안에** 남는다. 로그를 놓쳐도 되짚을 수 있어야 한다."""
    p = _prepared_or_skip()
    merged_rows = [r for r in p.rows if r["metadata"]["merged_from"]]
    assert sum(len(r["metadata"]["merged_from"]) for r in merged_rows) == p.merged
    if p.merged:
        # 대표는 단수로 남는다 — 골든셋 대조·8단계 검색이 단수를 전제한다
        assert all(isinstance(r["metadata"]["chunk_id"], str) for r in merged_rows)


def test_content_hash_is_unique_per_row() -> None:
    """UNIQUE 제약에 걸리기 전에 여기서 깨진다 — 트랜잭션 전체가 롤백되면 원인을 찾기 어렵다."""
    p = _prepared_or_skip()
    hashes = [r["content_hash"] for r in p.rows]
    assert len(set(hashes)) == len(hashes)


def test_embedding_model_is_what_was_actually_used() -> None:
    """**판정 승자가 아니라 실제로 쓴 모델**을 적는다 (RAG-008, RAG-024 판정 이후).

    DB 안의 벡터가 무엇으로 만들어졌는지는 사실의 문제지 계획의 문제가 아니다.
    """
    p = _prepared_or_skip("bge-m3")
    assert p.model_repo == "BAAI/bge-m3"
    assert all(r["metadata"]["embedding_model"] == "BAAI/bge-m3" for r in p.rows)
    assert all(r["metadata"]["embedding_model_key"] == "bge-m3" for r in p.rows)


def test_kpi_fields_reach_metadata() -> None:
    """`citation`·`citation_url` 은 **KPI 그 자체**(출처 링크 + 조항 인용)라 9단계가 답변에 싣는다.

    `chunk_id` 는 골든셋 주소이자 8단계가 결과를 되짚는 주소다. 셋 중 하나라도 빠지면
    "출처를 댈 수 있는 답변"이라는 이 프로젝트의 전제가 무너진다.
    """
    p = _prepared_or_skip()
    law = [r for r in p.rows if r["metadata"].get("citation", "").startswith("동물보호법")]
    assert law, "법령 청크가 하나도 없다"
    row = law[0]
    assert row["metadata"]["chunk_id"]
    assert row["metadata"]["citation"]
    assert row["metadata"]["citation_url"]


def test_source_reaches_the_column() -> None:
    """RAG-025 ④ — `.meta.json` 의 기관명이 파서·청커를 거쳐 `documents.source` 까지 온다.

    이 값은 원래 두 층에서 조용히 빠져 있었다. 다시 빠지면 여기서 깨진다.
    """
    p = _prepared_or_skip()
    sources = {r["source"] for r in p.rows}
    assert None not in sources and "" not in sources
    assert any("법제처" in s for s in sources)


def test_vector_is_1024_and_normalized() -> None:
    """RAG-002 — 세 모델 모두 1024 native. 정규화되어 있어야 `<=>` 가 곧 코사인이다."""
    import numpy as np

    p = _prepared_or_skip()
    v = np.asarray(p.rows[0]["embedding"], dtype="float32")
    assert v.shape == (embed.DIM,)
    assert np.isclose(np.linalg.norm(v), 1.0, atol=1e-3)


def test_columns_and_meta_do_not_overlap_by_accident() -> None:
    """컬럼으로 가는 값이 metadata 에도 중복되면 나중에 둘이 어긋난다."""
    p = _prepared_or_skip()
    row = p.rows[0]
    columns = set(load.COLUMNS) - {"metadata"}
    assert not (columns & set(row["metadata"])), "컬럼 값이 metadata 에도 있다"


# ---------------------------------------------------------------- DB 가 있을 때만
def _conn_or_skip():
    try:
        return load.connect()
    except Exception as exc:                       # 컨테이너가 안 떠 있으면 skip 이지 실패가 아니다
        pytest.skip(f"DB 에 연결할 수 없다 ({type(exc).__name__}) — `docker compose up -d`")


def test_loaded_rows_match_prepared() -> None:
    """적재된 행 수가 `prepare()` 가 만든 수와 같은가."""
    p = _prepared_or_skip()
    with _conn_or_skip() as conn:
        if load.count(conn) == 0:
            pytest.skip("아직 적재하지 않았다 — `python -m rag load` 먼저")
        assert load.count(conn) == len(p.rows)


def test_one_model_in_the_column() -> None:
    """**RAG-002 — 한 컬럼에 모델 혼입 금지.** 한 트랜잭션으로 넣는 이유가 이것이다.

    둘 이상 보이면 적재가 중간에 죽었거나 트랜잭션이 풀린 것이다.
    """
    with _conn_or_skip() as conn:
        models = load.existing_models(conn)
        if not models:
            pytest.skip("아직 적재하지 않았다")
        assert len(models) == 1, f"모델이 섞여 있다: {models}"


def test_upsert_is_idempotent() -> None:
    """같은 명령을 다시 돌려도 행이 늘지 않는다 — `content_hash` 가 자연키다 (RAG-008).

    RAG-025 ① 이 `DO NOTHING` 을 버린 이유는 이것 때문이 **아니다**(그건 교체 때문이다).
    여기서 보는 것은 중복 방지가 여전히 살아 있다는 것이다.
    """
    p = _prepared_or_skip()
    with _conn_or_skip() as conn:
        if load.count(conn) == 0:
            pytest.skip("아직 적재하지 않았다")
        before = load.count(conn)
        load.upsert(conn, p.rows[:20])
        assert load.count(conn) == before
