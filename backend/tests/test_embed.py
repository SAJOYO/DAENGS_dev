"""임베딩 테스트 — 계약과 가드만 본다 (RAG-002, RAG-021 ②).

**가중치를 로드하는 테스트는 여기 두지 않는다.** `bge-m3` 하나가 6.5GB 라 `pytest` 가 분 단위로
느려지고, 그러면 아무도 안 돌린다. 대신 나눈다.

  - 여기(기본) — 레지스트리·산출물 계약·토큰 가드. 토크나이저만 쓰거나 파일만 읽는다
  - `-m slow` — 실제 인코딩. `uv run pytest -m slow` 로 따로 돌린다

`data/` 는 git 미추적이라(RAG-017) 청크나 parquet 이 없으면 실패가 아니라 skip 이다.
"""
from __future__ import annotations

import pytest

# `ml` 그룹이 없으면 이 파일 전체를 건너뜁니다 (#230).
#
# **CI 는 torch 를 안 깝니다** — `ml` 은 임베딩 가중치까지 딸려 오는 무거운 그룹이라
# (D-021), 기본 설치로 도는 CI 에 넣을 것이 아닙니다. 그렇다고 그냥 두면 아래 테스트가
# `ModuleNotFoundError` 로 **깨져서** 전체 스위트가 빨간불이 됩니다 — 없는 그룹은
# 실패가 아니라 skip 이 맞고, `test_gait_inference.py` 가 `--group gait` 에 대해
# 같은 방식을 쓰고 있습니다.
#
# 로컬에서 이 파일을 돌리려면: `uv sync --group ml`
pytest.importorskip("pyarrow", reason="parquet 계약 검증에는 --group ml 이 필요합니다")
pytest.importorskip("transformers", reason="토크나이저 가드 검증에는 --group ml 이 필요합니다")

from daengs_life.rag.core import config
from daengs_life.rag.stages import embed

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# ---------------------------------------------------------------- 레지스트리 (RAG-002)
def test_three_models() -> None:
    """3파전이므로 셋이다. 늘거나 줄면 6단계 비교의 전제가 바뀐다."""
    assert list(embed.MODELS) == ["bge-m3", "kure-v1", "qwen3-embedding-0.6b"]


def test_official_repos() -> None:
    """정식 식별자는 `documents.metadata.embedding_model` 로 그대로 간다 (RAG-008)."""
    assert [m.repo for m in embed.MODELS.values()] == [
        "BAAI/bge-m3", "nlpai-lab/KURE-v1", "Qwen/Qwen3-Embedding-0.6B",
    ]


def test_qwen_query_prompt_is_official() -> None:
    """Qwen3 는 **질의에만** 지시문을 붙이는 비대칭 모델이다.

    이 문구는 모델 저장소의 `config_sentence_transformers.json` 을 그대로 옮긴 것이다.
    우리 도메인(한국 법령)에 맞게 손보면 그 모델만 튜닝을 받는 셈이라 3파전이 불공정해진다 —
    RAG-021 ② 가 문자 기준을 택한 것과 같은 논리다. 튜닝은 승자가 정해진 뒤(7단계) 할 일이다.
    """
    qwen = embed.MODELS["qwen3-embedding-0.6b"]
    assert qwen.query_prompt.startswith("Instruct: Given a web search query")
    assert qwen.doc_prompt == ""                      # 문서 쪽은 빈 문자열이 공식 설정이다
    for key in ("bge-m3", "kure-v1"):
        assert embed.MODELS[key].query_prompt == ""   # 나머지 둘은 프롬프트가 없다


def test_same_dim() -> None:
    """셋 다 1024 native 라서 나란히 비교할 수 있다 (RAG-002)."""
    assert embed.DIM == 1024


def test_bge_and_kure_share_limit() -> None:
    """KURE-v1 은 bge-m3 파생이라 토크나이저와 한계가 같다 (RAG-021 ② 실측)."""
    assert embed.MODELS["bge-m3"].max_tokens == embed.MODELS["kure-v1"].max_tokens == 8192
    assert embed.MODELS["qwen3-embedding-0.6b"].max_tokens == 32768


# ---------------------------------------------------------------- 토큰 가드 (RAG-021 ②)
def test_guard_rejects_instead_of_truncating() -> None:
    """한계를 넘으면 **실패시킨다.** 조용히 잘리면 뒷부분이 사라진 채 6단계 점수만 떨어진다.

    실물 청크는 최대 2,026 토큰(한계의 25%)이라 이 경로를 타지 않는다. 그래서 가짜 한계로 건드린다.
    """
    tiny = embed.Model("tiny", "BAAI/bge-m3", max_tokens=4)
    with pytest.raises(ValueError, match="입력 한계"):
        embed.guard(tiny, ["이 문장은 네 토큰보다 확실히 길다"])


def test_guard_passes_real_chunks() -> None:
    """실물 1,407건이 세 모델 한계 안에 있다 — RAG-021 ② 의 7,500자 상한이 실제로 작동한다는 확인."""
    rows = embed.load_chunks()
    if not rows:
        pytest.skip("chunks 가 없다 — `python -m rag chunk` 먼저")
    texts = [r["content"] for r in rows]
    for key in ("bge-m3", "qwen3-embedding-0.6b"):       # KURE 는 bge-m3 와 토크나이저가 같다
        model = embed.MODELS[key]
        stats = embed.guard(model, texts)
        assert stats["over"] == 0
        assert stats["max"] < model.max_tokens


# ---------------------------------------------------------------- 산출물 계약
#: 실제로 서빙에 쓰이는 모델. 결정은 `config.py` 한 군데에 있다 (RAG-024 · RAG-045 ②).
SERVING = config.settings.embedding_model_key


def _meta_or_skip(key: str) -> dict:
    """parquet 메타. **낡았으면 서빙 모델만 실패하고 나머지는 skip 한다** (RAG-047 ⑧).

    `embed.is_current` 가 청크 **전체**의 지문 하나로 판단해서, 소스가 하나 늘면 세 모델이
    전부 낡는다. 그런데 재인코딩은 모델당 20~40분이라(GPU 경합 시 두 배) 소스를 더할 때마다
    셋을 다 돌리는 것은 균형이 안 맞는다 — RAG-024 가 승자를 정한 뒤로 나머지 둘은
    **"다시 재 볼 때만"** 필요하기 때문이다.

    그래서 기준을 가른다:

      · 서빙 모델(`config.settings.embedding_model_key`) — **반드시 최신이어야 한다.**
        여기가 낡으면 문서 벡터와 질의 벡터가 다른 코퍼스를 가리키는데 **차원이 같아서
        (1024) 예외가 하나도 안 난다** (CLAUDE.md · RAG-024). 조용히 틀리는 자리다
      · 베이크오프 모델 — 낡았으면 skip 하고 **이유를 말한다.** 서빙에 안 쓰이므로 빨간
        불로 둘 이유가 없고, 반대로 조용히 통과시키면 "3파전을 다시 재도 된다"고 착각한다

    `data/` 가 미추적이라(RAG-017) 파일 자체가 없으면 예전처럼 그냥 skip 이다.
    """
    meta = embed.read_meta(key)
    if not meta:
        pytest.skip(f"{key}.parquet 이 없다 — `python -m rag embed` 먼저")
    if not embed.is_current(key, embed.chunks_fingerprint()):
        n = len(embed.load_chunks())
        stale = f"{key}.parquet 이 낡았다 — 코퍼스 {n}청크 / parquet {meta.get('chunk_count')}청크"
        if key == SERVING:
            pytest.fail(
                f"{stale}. **서빙 모델은 낡으면 안 된다** — 질의와 문서가 다른 코퍼스를 "
                f"가리키는데 차원이 같아 예외가 안 난다. `rag embed --model {key}` 로 다시 만들 것"
            )
        pytest.skip(f"{stale}. 베이크오프용이라 서빙에는 안 쓰인다 — 3파전을 다시 잴 때 만들면 된다")
    return meta


@pytest.mark.parametrize("key", list(embed.MODELS))
def test_parquet_contract(key: str) -> None:
    """6단계 베이크오프와 7단계 적재가 이 메타데이터만 보고 동작해야 한다."""
    meta = _meta_or_skip(key)
    model = embed.MODELS[key]
    assert meta["embedding_model"] == model.repo
    assert meta["dim"] == str(embed.DIM)
    assert meta["normalized"] == "l2"       # 정규화 = 내적이 곧 코사인
    assert meta["dtype"] == "float32"
    assert meta["query_prompt"] == model.query_prompt
    assert int(meta["chunk_count"]) == len(embed.load_chunks())


@pytest.mark.parametrize("key", list(embed.MODELS))
def test_normalized_and_aligned(key: str) -> None:
    """벡터가 실제로 단위 길이이고, 행 순서가 `chunk_id` 로 chunks 와 맞물린다."""
    import numpy as np
    import pyarrow.parquet as pq

    _meta_or_skip(key)              # 없거나 낡았으면 여기서 갈린다 (서빙만 실패)
    table = pq.read_table(embed.parquet_path(key))
    vectors = np.stack(table["embedding"].to_pylist()).astype("float32")
    assert vectors.shape == (table.num_rows, embed.DIM)
    norms = np.linalg.norm(vectors, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-3)

    ids = table["chunk_id"].to_pylist()
    assert len(set(ids)) == len(ids)                       # 골든셋이 가리킬 주소다
    assert ids == [r["chunk_id"] for r in embed.load_chunks()]


def test_three_files_same_chunks() -> None:
    """세 파일이 **같은 청크 한 벌**에서 나왔는지. 다르면 3파전이 성립하지 않는다.

    **3파전을 실제로 재는 시점에만 뜻이 있다.** 서빙 모델만 최신인 상태(소스를 더한 직후)가
    정상이므로, 그때는 skip 한다 — 여기서 실패시키면 "베이크오프용을 다시 만들라"는 압력이
    소스를 더할 때마다 생긴다 (RAG-047 ⑧).
    """
    fingerprint = embed.chunks_fingerprint()
    metas = [embed.read_meta(k) for k in embed.MODELS]
    if not all(metas):
        pytest.skip("parquet 3종이 다 있어야 비교한다")
    if not all(embed.is_current(k, fingerprint) for k in embed.MODELS):
        pytest.skip("3종이 다 최신일 때만 비교한다 — 지금은 서빙 모델만 최신이다")
    assert len({m["chunks_sha256"] for m in metas}) == 1


def test_skip_is_fingerprint_based() -> None:
    """재실행 스킵은 상류 산출물의 해시로 판단한다 — 별도 상태 파일이 없다 (RAG-001 원칙 2).

    **서빙 모델로 본다.** 아무 모델이나 쓰면 그 모델이 낡은 날 이 테스트가 "지문 판정이
    깨졌다"고 말하는데, 실제로는 판정이 제대로 동작한 것이다.
    """
    _meta_or_skip(SERVING)
    assert embed.is_current(SERVING, embed.chunks_fingerprint())
    assert not embed.is_current(SERVING, "다른지문")


# ---------------------------------------------------------------- 실제 인코딩 (느림)
@pytest.mark.slow
def test_query_prompt_changes_vector() -> None:
    """지시문이 실제로 벡터를 바꾸는지. 안 바뀌면 레지스트리에 적어 둔 의미가 없다.

    가장 작은 모델(1.2GB)로만 확인한다. `uv run pytest -m slow` 로 따로 돌린다.
    """
    import numpy as np

    model = embed.MODELS["qwen3-embedding-0.6b"]
    st = embed.load_model(model)
    q = "강아지 등록 안 하면 어떻게 되나요"
    with_prompt = embed.encode_query(model, q, st=st)
    without = embed.encode_docs(model, [q], st=st)[0]     # doc_prompt 는 빈 문자열
    assert float(np.dot(with_prompt, without)) < 0.999


# ---------------------------------------------------------------- 증분 (RAG-064)
# **모델을 안 올린다.** 벡터를 손으로 만들어 넣고 *병합이 행을 제자리에 놓는가*만 본다 —
# 수치 오차는 배치 정찰(RAG-064 ①)이 따로 쟀고, 여기서 볼 것은 그것과 다른 실패다.
def _fake_rows(specs: list[tuple[str, str]]) -> list[dict]:
    return [{"chunk_id": cid, "content": text, "chars": len(text), "element_type": "article"}
            for cid, text in specs]


def _write(tmp_path, monkeypatch, key: str, specs: list[tuple[str, str]], vectors):
    import numpy as np

    monkeypatch.setattr(config, "EMBED_DIR", tmp_path)
    embed.write_parquet(embed.MODELS[key], _fake_rows(specs),
                        np.asarray(vectors, dtype=np.float32),
                        fingerprint="지문0", token_stats={"max": 1, "median": 1, "p95": 1})


def _unit(seed: int):
    """길이 1 짜리 가짜 벡터. 값이 행마다 달라야 뒤섞임을 잡을 수 있다."""
    import numpy as np

    v = np.zeros(embed.DIM, dtype=np.float32)
    v[seed % embed.DIM] = 1.0
    return v


def test_content_sha256_is_written_per_row(tmp_path, monkeypatch) -> None:
    """행마다 `content` 해시가 실린다. **`chars` 는 대리 검사라 못 쓴다** (RAG-064).

    글자 수가 같은 수정(오타 한 글자 교체)에서 `chars` 는 안 움직이는데 해시는 움직인다 —
    이 차이가 없으면 낡은 벡터를 그대로 유지하면서 **예외가 하나도 안 난다.**
    """
    import pyarrow.parquet as pq

    _write(tmp_path, monkeypatch, SERVING, [("a#1", "가나다"), ("b#1", "라마바")],
           [_unit(0), _unit(1)])
    table = pq.read_table(tmp_path / f"{SERVING}.parquet")
    assert "content_sha256" in table.column_names
    assert table["content_sha256"].to_pylist() == [
        embed.content_sha256("가나다"), embed.content_sha256("라마바")]
    # 같은 글자 수, 다른 내용 → chars 는 같고 해시는 다르다
    assert len("가나다") == len("가나닥"[:3])
    assert embed.content_sha256("가나다") != embed.content_sha256("가나달")


def test_plan_reuses_unchanged_and_encodes_only_the_changed(tmp_path, monkeypatch) -> None:
    """바뀐 행만 인코딩 대상이 된다. 나머지는 parquet 의 벡터를 재사용한다."""
    _write(tmp_path, monkeypatch, SERVING,
           [("a#1", "그대로"), ("b#1", "바뀔것"), ("c#1", "사라질것")],
           [_unit(0), _unit(1), _unit(2)])
    rows = _fake_rows([("a#1", "그대로"), ("b#1", "바뀌었다"), ("d#1", "새로생김")])

    plan = embed.plan_incremental(SERVING, embed.MODELS[SERVING], rows)
    assert plan.ok, plan.refused
    assert plan.reuse == [(0, 0)]              # a#1 만 그대로
    assert plan.encode == [1, 2]               # b#1(내용 변경) · d#1(신규)
    assert plan.dropped == 2                   # b#1 의 옛 행 · c#1


def test_merge_puts_every_row_in_its_place(tmp_path, monkeypatch) -> None:
    """**병합이 행을 뒤섞지 않는다.** 순서는 늘 현재 청크 순서다.

    여기가 틀리면 벡터와 `chunk_id` 가 어긋나는데 **차원이 같아 예외가 안 나고**, 검색이
    엉뚱한 문서를 1위로 올릴 뿐이다 — 이 저장소가 처음부터 경계하는 모양이다.
    """
    import numpy as np
    import pyarrow.parquet as pq

    _write(tmp_path, monkeypatch, SERVING,
           [("a#1", "그대로"), ("b#1", "바뀔것")], [_unit(0), _unit(1)])
    # 새 순서: 바뀐 것이 **앞**으로 온다 — 재사용 행이 뒤로 밀리는 배치다
    rows = _fake_rows([("b#1", "바뀌었다"), ("a#1", "그대로")])
    plan = embed.plan_incremental(SERVING, embed.MODELS[SERVING], rows)
    assert plan.reuse == [(1, 0)] and plan.encode == [0]

    embed.write_parquet_incremental(
        embed.MODELS[SERVING], rows, plan, np.asarray([_unit(7)]),
        fingerprint="지문1", token_stats={"max": 1, "median": 1, "p95": 1})

    table = pq.read_table(tmp_path / f"{SERVING}.parquet")
    assert table["chunk_id"].to_pylist() == ["b#1", "a#1"]
    got = np.asarray(table["embedding"].to_pylist(), dtype=np.float32)
    assert np.array_equal(got[0], _unit(7))    # 새로 만든 벡터가 b#1 자리에
    assert np.array_equal(got[1], _unit(0))    # 재사용 벡터가 a#1 자리에 — 옛 index 0 에서 왔다


def test_incremental_refuses_when_mixing_would_be_silent(tmp_path, monkeypatch) -> None:
    """거부 조건은 전부 **섞으면 조용히 틀리는** 자리다 (RAG-064).

    특히 모델이 다른 경우 — 세 모델 모두 1024차원이라 **섞여도 예외가 안 난다.**
    CLAUDE.md 가 "차원이 같아서 조용히 틀린다"고 경고하는 그 자리다.
    """
    monkeypatch.setattr(config, "EMBED_DIR", tmp_path)
    rows = _fake_rows([("a#1", "그대로")])

    # ① parquet 자체가 없다
    assert not embed.plan_incremental(SERVING, embed.MODELS[SERVING], rows).ok

    # ② 다른 모델로 만든 파일 — 파일명은 SERVING 인데 메타의 repo 가 다르다
    other = next(k for k in embed.MODELS if k != SERVING)
    _write(tmp_path, monkeypatch, SERVING, [("a#1", "그대로")], [_unit(0)])
    import pyarrow.parquet as pq
    path = tmp_path / f"{SERVING}.parquet"
    table = pq.read_table(path)
    meta = {k: v for k, v in (table.schema.metadata or {}).items()}
    meta[b"embedding_model"] = embed.MODELS[other].repo.encode()
    pq.write_table(table.replace_schema_metadata(meta), path)

    plan = embed.plan_incremental(SERVING, embed.MODELS[SERVING], rows)
    assert not plan.ok and "모델이 다르다" in plan.refused


def test_backfill_refuses_when_the_fingerprint_disagrees(tmp_path, monkeypatch) -> None:
    """지문이 안 맞으면 해시를 **안 채운다.**

    채우면 "낡은 벡터에 최신 해시" 가 붙어 증분이 그 행들을 **영원히 건너뛴다** —
    고치려던 병보다 나쁘다.
    """
    _write(tmp_path, monkeypatch, SERVING, [("a#1", "그대로")], [_unit(0)])
    rows = _fake_rows([("a#1", "그대로")])
    # 방금 쓴 파일은 이미 v2 다
    ok, why = embed.backfill_hashes(SERVING, "지문0", rows)
    assert not ok and "이미 v2" in why
