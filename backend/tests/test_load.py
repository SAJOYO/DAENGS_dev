"""7단계 적재 테스트 — 계약과 규칙을 본다 (RAG-008, RAG-025).

**두 층으로 나눈다.** `prepare()` 는 DB 없이 검사할 수 있고(그래서 `parse_doc` 처럼 쓰기를
분리해 뒀다), 연결이 필요한 것은 DB 가 없으면 skip 한다 — `data/` 가 미추적이듯(RAG-017)
컨테이너도 항상 떠 있지는 않다.

**여기서 지키는 것 중 가장 중요한 하나는 `metadata.embedding_model` 이 "실제로 쓴 모델"이라는
것이다.** 그 둘이 어긋나면 DB 안의 벡터가 무엇으로 만들어졌는지를 아무도 못 믿게 된다.

**모델은 `config.settings.embedding_model_key` 에서 받는다 — 여기에 적지 않는다** (RAG-045).
예전에는 `bge-m3` 를 박아 뒀고 그때는 맞았다(적재가 기준선으로 돌던 시기다). 2026-08-28 에
적재가 `qwen3` 로 바뀌었는데 **이 파일이 안 따라와서**, 테스트가 `bge-m3` 로 질의해 qwen3 인덱스를
뒤지는 상태가 됐다. 차원이 같아(1024) 예외는 안 나고 검색 결과만 무의미해진다 — 그 어긋남을
`bge-m3.parquet` 이 없어 skip 되던 것이 가려 주고 있었다. 결정은 `config.py` 한 군데에만 둔다.
"""
from __future__ import annotations

import hashlib

import pytest

from daengs_life.rag.core import config
from daengs_life.rag.stages import embed, load

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


class _Rollback(Exception):
    """`test_upsert_is_idempotent` 이 **운영 DB 에 흔적을 안 남기려고** 쓰는 탈출구 (RAG-045).

    DB 는 팀에 하나뿐이라(CLAUDE.md) 누가 `pytest` 만 돌려도 인덱스가 바뀌면 안 된다. 실제로
    2026-08-29 에 이 테스트가 `benefit24-services` 20행을 다른 모델의 벡터로 덮었고, **행 수만
    보는 단언이라 통과했다.** `load.upsert` 가 자기 `conn.transaction()` 을 열므로 여기서 바깥
    트랜잭션을 한 겹 두르면 그쪽이 savepoint 가 되고, 이 예외로 빠져나가면 통째로 되돌아간다.
    """


#: 서빙 모델은 결정이다 (RAG-024 · D-021). 테스트도 그 결정을 읽지, 따로 고르지 않는다.
MODEL_KEY = config.settings.embedding_model_key


def _prepared_or_skip(key: str = MODEL_KEY):
    """`prepare()` 한 벌. **낡은 parquet 은 서빙 모델만 실패시킨다** (RAG-047 ⑧).

    `test_embed._meta_or_skip` 과 같은 규칙이다 — `embed.is_current` 가 청크 전체 지문
    하나로 판단해서 소스가 하나 늘면 세 모델이 다 낡는데, 재인코딩이 모델당 20~40분이라
    소스를 더할 때마다 셋을 다 돌리는 것은 균형이 안 맞는다. 서빙에 쓰는 것만 최신을
    요구하고, 베이크오프용은 skip 하고 이유를 말한다.

    낡은 채로 `load.prepare()` 를 부르면 `ValueError: chunk_id 순서가 chunks/ 와 다르다`
    로 죽는다 — 맞는 동작이지만 여기서는 "적재 계약이 깨졌다"가 아니라 "저 모델을 아직 안
    다시 만들었다"는 뜻이라, 실패로 두면 신호가 뒤바뀐다.
    """
    if not embed.parquet_path(key).is_file():
        pytest.skip(f"{key}.parquet 이 없다 — `python -m rag embed` 먼저")
    if not embed.is_current(key, embed.chunks_fingerprint()):
        stale = f"{key}.parquet 이 낡았다 — 코퍼스가 그 뒤로 움직였다"
        if key == MODEL_KEY:
            pytest.fail(f"{stale}. **서빙 모델은 낡으면 안 된다** — `rag embed --model {key}`")
        pytest.skip(f"{stale}. 베이크오프용이라 서빙에는 안 쓰인다")
    return load.prepare(key)


# ---------------------------------------------------------------- 기본값 = 결정 (RAG-024 · D-021)
def test_default_model_is_the_decision_not_an_accident() -> None:
    """기본값 한 줄이 곧 "교체가 싸다"의 장치다. **가드는 남기고 지키는 값만 바뀐다.**

    전에 이 자리는 *"첫 관통은 기준선(`bge-m3`)으로 간다"* 였다 — RAG-024 가 판정 승자를
    `qwen3` 로 내고도 **재적재 비용** 때문에 운영을 기준선으로 뒀기 때문이다. 코퍼스를 0에서
    다시 만드는 지금(#34) 그 비용이 0 이라 조건이 사라졌고, 그래서 승자로 올린다.

    **이 테스트가 실제로 붙잡는 것은 이름이 실재하는가**(`embed.MODELS` 안에 있는가)이다.
    오타는 적재·서빙 양쪽에서 `KeyError` 로 늦게 죽는다.

    **서빙 키가 코퍼스와 같은지는 여기서 못 본다** — 그건 DB 를 봐야 알고, 어긋나도 차원이
    같아서(셋 다 1024) 예외가 안 난다. 그 자리의 가드는 기동 때 도는
    `daengs_life.app.deps.warn_if_corpus_uses_another_model` 이다.
    """
    assert config.settings.embedding_model_key == "qwen3-embedding-0.6b"
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


def test_stale_finds_rows_this_load_did_not_touch() -> None:
    """`stale()` 은 **이번 적재에 없는 행**을 찾는다 — 개정으로 사라진 청크다 (RAG-045 ①).

    행 하나를 일부러 빼고 부르면 그 행이 나와야 한다. `upsert` 가 지우지 않는다는 사실을
    뒤집어 확인하는 자리다.
    """
    p = _prepared_or_skip()
    with _conn_or_skip() as conn:
        if load.count(conn) == 0:
            pytest.skip("아직 적재하지 않았다")
        victim = p.rows[0]
        left = load.stale(conn, p.rows[1:])
        assert victim["content_hash"] in {h for h, _ in left}
        # 전량을 넘기면 그 행은 빠진다 — 나머지는 실제 유령이라 개수를 박지 않는다
        assert victim["content_hash"] not in {h for h, _ in load.stale(conn, p.rows)}


def test_prune_deletes_only_what_stale_returned() -> None:
    """`prune()` 은 넘긴 것만 지운다. **되돌려 확인한다** — 운영 DB 다 (RAG-045 ③)."""
    p = _prepared_or_skip()
    with _conn_or_skip() as conn:
        if load.count(conn) == 0:
            pytest.skip("아직 적재하지 않았다")
        before = load.count(conn)
        target = [(p.rows[0]["content_hash"], p.rows[0]["metadata"]["chunk_id"])]
        try:
            with conn.transaction():
                assert load.prune(conn, target) == 1
                assert load.count(conn) == before - 1
                raise _Rollback
        except _Rollback:
            pass
        assert load.count(conn) == before


def test_prune_on_empty_list_is_a_no_op() -> None:
    """빈 목록에 `DELETE ... = ANY('{}')` 를 보내지 않는다 — 실수로 전량을 지울 자리다."""
    with _conn_or_skip() as conn:
        before = load.count(conn)
        assert load.prune(conn, []) == 0
        assert load.count(conn) == before


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
        try:
            with conn.transaction():                 # ← 바깥 트랜잭션
                load.upsert(conn, p.rows[:20])       #    upsert 의 것은 savepoint 가 된다
                assert load.count(conn) == before
                raise _Rollback                      #    확인이 끝나면 되돌린다
        except _Rollback:
            pass
        assert load.count(conn) == before            # 되돌린 뒤에도 그대로
