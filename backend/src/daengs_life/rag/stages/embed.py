"""4단계 임베딩 — `chunks/` → `embeddings/{key}.parquet` (RAG-002, RAG-021 ②).

**DB 밖에서 오프라인으로 만든다** (RAG-002). 6단계 베이크오프가 7단계 적재보다 먼저이므로, 세 모델의
벡터가 DB 에 들어가기 전에 파일로 나란히 존재해야 비교할 수 있다. `documents.embedding` 은 한 컬럼이라
모델을 섞을 수 없다.

**청크 한 벌을 세 모델이 공유한다.** RAG-021 ② 가 문자 기준으로 자르기로 한 이유가 이것이다 —
어느 토크나이저로 자르면 그 모델에 맞춰진 청크가 되어 3파전이 오염된다. 같은 이유로 **각 모델의
공식 프롬프트는 그대로 쓴다**(아래 `MODELS`). 우리 도메인에 맞게 손보면 그 모델만 튜닝을 받는 셈이다.

토큰은 자르는 자가 아니라 **가드**다. RAG-004 기준④(임베딩 입력 한계)가 실제로 집행되는 자리가 여기고,
한계를 넘으면 **조용히 잘리게 두지 않고 실패시킨다.**
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core import config, io

VERSION = 1
DIM = 1024                # RAG-002 — 세 모델 모두 1024 native. 다르면 비교 자체가 성립하지 않는다


@dataclass(frozen=True)
class Model:
    """`query_prompt` 는 모델 저장소의 `config_sentence_transformers.json` 을 **그대로** 옮긴 것이다.

    Qwen3 만 비대칭이다 — 문서는 프롬프트 없이, 질의에만 지시문을 붙인다. 이 사실이 코드에 없으면
    6단계에서 질의를 프롬프트 없이 넣게 되고, Qwen3 를 자기 설계와 다르게 쓰면서 점수를 매기게 된다.
    """
    key: str              # 파일명·CLI 인자 (슬래시를 파일명에 쓸 수 없다)
    repo: str             # 정식 식별자 — `documents.metadata.embedding_model` 에 이 값이 간다 (RAG-008)
    max_tokens: int       # 실측: sentence_bert_config.json / config.json 의 한계
    query_prompt: str = ""
    doc_prompt: str = ""


MODELS: dict[str, Model] = {
    "bge-m3": Model("bge-m3", "BAAI/bge-m3", 8192),
    "kure-v1": Model("kure-v1", "nlpai-lab/KURE-v1", 8192),
    "qwen3-embedding-0.6b": Model(
        "qwen3-embedding-0.6b", "Qwen/Qwen3-Embedding-0.6B", 32768,
        query_prompt=("Instruct: Given a web search query, "
                      "retrieve relevant passages that answer the query\nQuery:"),
    ),
}


# ---------------------------------------------------------------- 입력
def load_chunks() -> list[dict[str, Any]]:
    """청크를 **파일 경계 없이** 전부 이어 붙인다.

    RAG-021 ⑤A 가 청크 행을 자기완결적으로 만든 이유가 이것이다 — 여기서 헤더를 들고 다닐 필요가 없다.
    """
    rows: list[dict[str, Any]] = []
    for path in io.chunk_files():
        rows += list(io.read_chunks(path))
    return rows


FINGERPRINT_VERSION = 2   # 1 = 청크 파일 해시 / 2 = (chunk_id, content) 쌍 (RAG-025 ⑤)


def chunks_fingerprint() -> str:
    """**임베딩이 실제로 의존하는 것만** 해싱한다 — `chunk_id` 와 `content` (RAG-025 ⑤).

    v1 은 청크 *파일* 해시를 이어 붙였다. 그러면 `source` 같은 메타 필드를 한 줄 더할 때마다
    벡터가 무효화되어 1,407청크를 세 모델로 다시 인코딩해야 한다 — **`content` 는 한 글자도
    안 바뀌는데 같은 숫자를 수십 분에 걸쳐 다시 만드는 것이다.** 실제로 RAG-025 ④(파서·청커에
    `source` 싣기)에서 그 대가가 드러나 정의를 좁혔다.

    **`chunk_id` 를 함께 넣는 이유** — `content` 만 보면 재수집으로 날짜가 바뀌어 `chunk_id` 가
    달라져도 지문이 같게 나오고, parquet 의 행과 청크가 어긋난 채 "최신"으로 통과한다.
    지문은 "임베딩이 의존하는 것 전부"여야 하고 행의 주소도 거기 포함된다.

    RAG-001 원칙 2(상류 해시를 하류 헤더에 적어 비교)를 부정하는 것이 아니라 **정밀화**다.
    축은 그대로이고 "상류 산출물"의 정의가 파일에서 **임베딩 입력**으로 좁아졌다.
    """
    h = hashlib.sha256()
    for path in io.chunk_files():
        for row in io.read_chunks(path):
            # 널 바이트로 끊는다 — 이어 붙이기만 하면 경계가 옮겨진 다른 조합이 같은 해시를 낸다
            h.update(row["chunk_id"].encode())
            h.update(b"\0")
            h.update(row["content"].encode())
            h.update(b"\0")
    return h.hexdigest()


# ---------------------------------------------------------------- 증분 (RAG-064)
# **전역 지문은 "다시 만들어야 하나"만 답한다. 증분은 "무엇을"까지 답해야 한다.**
# 그래서 행마다 `content` 해시를 parquet 에 싣는다.
#
# ⚠ **`chars` 로 대신하지 않는다.** `restamp` 는 `chars` 를 쓰면서 스스로 "내용 동일성의
#   대리 검사" 라고 부른다 — 일회성 검사에서는 감당할 만한 대리였지만, **매 적재마다 수천
#   행을 그것으로 판정하면** 글자 수가 같은 수정(오타 한 글자 교체)에서 낡은 벡터를 그대로
#   유지하고 **예외가 하나도 안 난다.** CLAUDE.md 가 경고하는 "차원이 같아서 조용히 틀린다"
#   와 같은 종류다.
SCHEMA_VERSION = 2        # 1 = chunk_id·embedding·chars·element_type / 2 = + content_sha256


def content_sha256(content: str) -> str:
    """행 하나의 내용 해시. 주소(`chunk_id`)는 별도 컬럼이라 여기 안 섞는다."""
    return hashlib.sha256(content.encode()).hexdigest()


# ---------------------------------------------------------------- 토큰 가드 (RAG-021 ②)
def token_stats(model: Model, texts: list[str]) -> dict[str, int]:
    """모델의 토크나이저로 재기만 한다. 가중치를 로드하지 않으므로 인코딩 전에 싸게 실패할 수 있다."""
    from transformers import AutoTokenizer

    tok = AutoTokenizer.from_pretrained(model.repo)
    lengths: list[int] = []
    for text in texts:
        lengths.append(len(tok(model.doc_prompt + text, add_special_tokens=True)["input_ids"]))
    return {
        "max": max(lengths),
        "p95": sorted(lengths)[int(len(lengths) * 0.95)],
        "median": sorted(lengths)[len(lengths) // 2],
        "over": sum(1 for n in lengths if n > model.max_tokens),
    }


def guard(model: Model, texts: list[str]) -> dict[str, int]:
    stats = token_stats(model, texts)
    if stats["over"]:
        raise ValueError(
            f"{model.repo}: 입력 한계 {model.max_tokens} 토큰을 넘는 청크 {stats['over']}건 "
            f"(최대 {stats['max']}). 잘라서 넘기지 않는다 — RAG-021 ② 는 여기서 실패시키기로 했다"
        )
    return stats


# ---------------------------------------------------------------- 인코딩
def load_model(model: Model, device: str | None = None):
    """`device` 를 주면 그대로 쓴다 — **서빙은 CPU 로 고정한다** (RAG-028 ①).

    자동 선택을 두되 강제할 수 있게 한 이유: 배치(4·6단계)는 GPU 가 필요하지만 서빙은 질의 하나라
    CPU 로 124ms 면 끝나고, 서버가 2.3GB 를 물고 있으면 2랩에서 새 소스를 임베딩할 때 6GB 중
    3.7GB 만 남는다. device 를 바꿔도 검색 결과가 안 바뀌는 것은 실측으로 확인했다
    (검증질문 7개 top-5 가 순서까지 동일, 검문소③ 2/7 재현).
    """
    from sentence_transformers import SentenceTransformer

    import torch

    device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    # fp32 를 쓴다. 1,407건이라 fp16 의 속도 이득이 의미 없고, 3파전 점수 차이를 정밀도 차이로
    # 오염시키지 않는다 — RAG-021 ② 가 문자 기준을 택한 것과 같은 논리다
    return SentenceTransformer(model.repo, device=device)


def release() -> None:
    """앞 모델을 GPU 에서 내린다. **호출 전에 caller 가 참조를 끊어야 한다** (`del st`).

    PyTorch 는 파이썬 객체가 사라져도 `empty_cache()` 전까지 VRAM 을 붙들고 있다. 3종을 한
    프로세스에서 차례로 돌리면 누적되고, 마지막 모델이 남은 공간에서 스와핑한다 — RTX 3050 6GB 에서
    실측으로 겪었다(bge-m3 2분 → KURE 6분 → Qwen3 23분+, VRAM 5,887/6,144 MiB). 6단계에서
    세 모델로 질의를 임베딩할 때도 같은 처리가 필요하다.
    """
    import gc

    import torch

    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def vram_used_mb() -> float:
    import torch

    return torch.cuda.memory_allocated() / 1e6 if torch.cuda.is_available() else 0.0


def encode_docs(model: Model, texts: list[str], batch_size: int = 8, st=None,
                progress: bool = False):
    """문서 임베딩. **L2 정규화해서 돌려준다** — 세 모델 모두 공식 지표가 cosine 이라
    정규화하면 내적 = 코사인이 되고, 6·7·8단계 세 곳에서 정규화 코드가 반복되지 않는다."""
    st = st or load_model(model)
    return st.encode(
        texts,
        batch_size=batch_size,
        prompt=model.doc_prompt or None,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=progress,
    )


def encode_query(model: Model, text: str, st=None):
    """질의 임베딩. **6단계가 이 함수를 쓴다.**

    Qwen3 는 질의에만 지시문을 붙이는 모델이라, 이 경로를 문서와 나누지 않으면 그 사실이 사라진다.
    """
    st = st or load_model(model)
    return st.encode(
        [text],
        prompt=model.query_prompt or None,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )[0]


# ---------------------------------------------------------------- 산출물
def parquet_path(key: str) -> Path:
    return config.EMBED_DIR / f"{key}.parquet"


def write_parquet(model: Model, rows: list[dict[str, Any]], vectors, *,
                  fingerprint: str, token_stats: dict[str, int]) -> Path:
    """`chunk_id` + 벡터 최소 스키마.

    `content` 를 넣지 않는다 — 세 파일에 3번 복제되고, 6단계는 `chunk_id` 로 chunks 에서 조회하면 된다.
    모델 정식 식별자는 **파일 메타데이터**에 적는다. 파일명은 사람이 읽고(슬래시를 못 쓴다),
    `documents.metadata.embedding_model` 에 갈 값은 기계가 읽는다 (RAG-008).
    """
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq

    vectors = np.asarray(vectors, dtype=np.float32)
    if vectors.shape != (len(rows), DIM):
        raise ValueError(f"{model.repo}: 벡터 모양 {vectors.shape} != {(len(rows), DIM)}")

    table = pa.table(
        {
            "chunk_id": pa.array([r["chunk_id"] for r in rows], pa.string()),
            "embedding": pa.FixedSizeListArray.from_arrays(
                pa.array(vectors.reshape(-1), pa.float32()), DIM),
            "chars": pa.array([r["chars"] for r in rows], pa.int32()),
            "element_type": pa.array([r["element_type"] for r in rows], pa.string()),
            # 증분이 "무엇이 바뀌었나"를 묻는 칸 (RAG-064). `chars` 는 대리 검사라 못 쓴다
            "content_sha256": pa.array(
                [content_sha256(r["content"]) for r in rows], pa.string()),
        },
        metadata={
            b"embedding_model": model.repo.encode(),
            b"model_key": model.key.encode(),
            b"dim": str(DIM).encode(),
            b"normalized": b"l2",
            b"dtype": b"float32",
            b"chunks_sha256": fingerprint.encode(),
            b"fingerprint_version": str(FINGERPRINT_VERSION).encode(),
            b"chunk_count": str(len(rows)).encode(),
            b"max_tokens": str(model.max_tokens).encode(),
            b"query_prompt": model.query_prompt.encode(),
            b"token_stats": json.dumps(token_stats, ensure_ascii=False).encode(),
            b"embedder_version": str(VERSION).encode(),
            b"schema_version": str(SCHEMA_VERSION).encode(),
            b"embedded_at": io.now_kst().encode(),
        },
    )
    path = parquet_path(model.key)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)
    return path


def read_meta(key: str) -> dict[str, str] | None:
    """parquet 파일 메타데이터. 재실행 스킵 판단과 6단계가 읽는다."""
    import pyarrow.parquet as pq

    path = parquet_path(key)
    if not path.is_file():
        return None
    raw = pq.read_schema(path).metadata or {}
    return {k.decode(): v.decode() for k, v in raw.items()}


def is_current(key: str, fingerprint: str) -> bool:
    meta = read_meta(key)
    return bool(meta) and meta.get("chunks_sha256") == fingerprint \
        and meta.get("embedder_version") == str(VERSION)


@dataclass(frozen=True)
class Plan:
    """증분 인코딩 계획 (RAG-064).

    `refused` 가 차 있으면 **전량으로 떨어져야 한다** — 그 이유를 사람이 로그에서 본다.
    `reuse` 는 `(현재 행 index, parquet 행 index)` 쌍이고, `encode` 는 현재 행 index 다.
    둘을 합치면 현재 청크 전체가 정확히 한 번씩 덮인다.
    """
    reuse: list[tuple[int, int]]
    encode: list[int]
    dropped: int
    refused: str | None = None

    @property
    def ok(self) -> bool:
        return self.refused is None


def plan_incremental(key: str, model: Model, rows: list[dict[str, Any]]) -> Plan:
    """무엇을 다시 인코딩해야 하나. **거부할 이유가 있으면 먼저 거부한다.**

    거부 조건은 전부 *"섞으면 조용히 틀리는"* 자리다:

      parquet 없음         첫 인코딩이다
      스키마가 옛것         `content_sha256` 칸이 없어 무엇이 바뀌었는지 물을 수 없다
      모델·차원이 다름      다른 모델의 벡터를 한 파일에 섞으면 코사인이 무의미해진다.
                          **차원이 같아서(1024) 예외가 안 난다** — CLAUDE.md 가 경고하는 자리
      embedder_version     인코딩 방식 자체가 바뀌었다는 뜻이다

    ⚠ **낡은 parquet 을 조용히 이어 쓰지 않는다.** `bge-m3`·`kure-v1` 은 2026-08-29 코퍼스
    (6,368행)에서 멈춰 있는데, 거기에 증분을 얹으면 **9,451행짜리 최신인 척하는 파일**이 된다.
    """
    import pyarrow.parquet as pq

    path = parquet_path(key)
    if not path.is_file():
        return Plan([], list(range(len(rows))), 0, refused="parquet 이 없다 (첫 인코딩)")

    meta = read_meta(key) or {}
    if meta.get("schema_version") != str(SCHEMA_VERSION):
        # **해법까지 말한다.** parquet 은 git 미추적이라(RAG-017) 다른 PC 는 여전히 v1 이고,
        # 원인만 말하면 그 사람은 이유를 모른 채 55분 전량 인코딩을 낸다 — A1 이 없애려던 값이다.
        return Plan([], list(range(len(rows))), 0,
                    refused=f"parquet 스키마가 v{meta.get('schema_version', '1')} 다"
                            f" — `content_sha256` 이 없어 증분을 못 판단한다."
                            f" 코퍼스가 이 parquet 과 같다면 `rag embed --backfill-hashes"
                            f" --model {key}` 로 몇 초에 채울 수 있다 (벡터 무변경)")
    if meta.get("embedding_model") != model.repo:
        return Plan([], list(range(len(rows))), 0,
                    refused=f"모델이 다르다: parquet={meta.get('embedding_model')} != {model.repo}")
    if meta.get("dim") != str(DIM) or meta.get("embedder_version") != str(VERSION):
        return Plan([], list(range(len(rows))), 0,
                    refused=f"dim/embedder_version 이 다르다:"
                            f" {meta.get('dim')}/{meta.get('embedder_version')}"
                            f" != {DIM}/{VERSION}")

    table = pq.read_table(path, columns=["chunk_id", "content_sha256"])
    have: dict[str, int] = {}
    for i, (cid, sha) in enumerate(zip(table["chunk_id"].to_pylist(),
                                       table["content_sha256"].to_pylist())):
        have[f"{cid}\0{sha}"] = i

    reuse, encode = [], []
    for i, row in enumerate(rows):
        j = have.get(f"{row['chunk_id']}\0{content_sha256(row['content'])}")
        if j is None:
            encode.append(i)
        else:
            reuse.append((i, j))
    return Plan(reuse, encode, dropped=table.num_rows - len(reuse))


def write_parquet_incremental(model: Model, rows: list[dict[str, Any]], plan: Plan,
                              new_vectors, *, fingerprint: str,
                              token_stats: dict[str, int]) -> Path:
    """재사용 벡터 + 새로 만든 벡터를 **현재 청크 순서로** 합쳐 쓴다.

    순서를 청크에 맞추는 것이 계약이다 — `restamp` 가 `chunk_id` 목록을 **순서까지** 대조하고,
    적재기도 parquet 행과 청크를 같은 순서로 본다. 증분이 순서를 흔들면 그 대조가 죽는다.
    """
    import numpy as np
    import pyarrow.parquet as pq

    old = pq.read_table(parquet_path(model.key), columns=["embedding"])
    old_vecs = np.asarray(old["embedding"].to_pylist(), dtype=np.float32)
    new_vecs = np.asarray(new_vectors, dtype=np.float32).reshape(len(plan.encode), DIM) \
        if len(plan.encode) else np.zeros((0, DIM), dtype=np.float32)

    merged = np.empty((len(rows), DIM), dtype=np.float32)
    for i, j in plan.reuse:
        merged[i] = old_vecs[j]
    for n, i in enumerate(plan.encode):
        merged[i] = new_vecs[n]
    return write_parquet(model, rows, merged,
                         fingerprint=fingerprint, token_stats=token_stats)


def restamp(key: str, fingerprint: str, rows: list[dict[str, Any]]) -> tuple[bool, str]:
    """벡터는 그대로 두고 **파일 메타데이터의 지문만** 다시 찍는다 (RAG-025 ⑤의 일회성 대가).

    지문 *정의*가 바뀌면 기존 parquet 에 적힌 값은 옛 방식이라 그대로는 stale 이다. 그렇다고
    같은 벡터를 다시 만드는 것은 낭비라 메타만 갱신한다.

    **다만 이 함수는 낡음을 덮는 도구가 되기 쉽다.** 그래서 찍기 전에 확인한다 —
    `chunk_id` 목록이 **순서까지** 같고 `chars` 도 같아야 한다. parquet 에 `content` 자체는
    없으므로(RAG-002 가 중복 저장을 피했다) `chars` 가 내용 동일성의 대리 검사다. 하나라도
    어긋나면 찍지 않고 이유를 돌려준다 — 그때는 진짜로 다시 인코딩해야 하는 상황이다.
    """
    import pyarrow.parquet as pq

    path = parquet_path(key)
    if not path.is_file():
        return False, "parquet 이 없다"
    table = pq.read_table(path)
    ids = table["chunk_id"].to_pylist()
    if ids != [r["chunk_id"] for r in rows]:
        return False, "chunk_id 목록이 다르다 — 메타만 갱신하면 행이 어긋난다. 다시 인코딩할 것"
    if table["chars"].to_pylist() != [r["chars"] for r in rows]:
        return False, "chars 가 다르다 — content 가 바뀌었다. 다시 인코딩할 것"

    meta = {k: v for k, v in (table.schema.metadata or {}).items()}
    before = meta.get(b"chunks_sha256", b"").decode()
    meta[b"chunks_sha256"] = fingerprint.encode()
    meta[b"fingerprint_version"] = str(FINGERPRINT_VERSION).encode()
    meta[b"restamped_at"] = io.now_kst().encode()
    pq.write_table(table.replace_schema_metadata(meta), path)
    return True, f"{before[:16]} -> {fingerprint[:16]}"


def backfill_hashes(key: str, fingerprint: str, rows: list[dict[str, Any]]) -> tuple[bool, str]:
    """옛 parquet(스키마 v1)에 `content_sha256` 칸을 **재인코딩 없이** 채운다 (RAG-064).

    **전역 지문이 증명서다.** `chunks_sha256` 이 현재 청크의 지문과 같으면, 그것은 정의상
    *모든* `(chunk_id, content)` 쌍이 동일하다는 뜻이다(`chunks_fingerprint` 참고). 그러니
    현재 청크로 행별 해시를 계산해 써넣어도 된다 — **`chars` 대리 검사를 안 거쳐도 된다.**
    전역 지문이 이미 그 일을 해 줬다.

    지문이 안 맞으면 **채우지 않는다.** 그때 채우면 "낡은 벡터에 최신 해시" 가 붙어 증분이
    그 행들을 영원히 건너뛴다 — 고치려던 병보다 나쁘다.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = parquet_path(key)
    if not path.is_file():
        return False, "parquet 이 없다"
    meta_now = read_meta(key) or {}
    if meta_now.get("schema_version") == str(SCHEMA_VERSION):
        return False, "이미 v2 다 — 채울 것이 없다"
    if meta_now.get("chunks_sha256") != fingerprint:
        return False, ("지문이 다르다 — 이 parquet 은 지금 청크로 만든 것이 아니다."
                       " 채우면 낡은 벡터에 최신 해시가 붙는다. 전량 인코딩할 것")

    table = pq.read_table(path)
    if table["chunk_id"].to_pylist() != [r["chunk_id"] for r in rows]:
        return False, "chunk_id 목록이 다르다 — 지문은 같은데 순서가 다르다. 전량 인코딩할 것"

    table = table.append_column(
        "content_sha256",
        pa.array([content_sha256(r["content"]) for r in rows], pa.string()))
    meta = {k: v for k, v in (table.schema.metadata or {}).items()}
    meta[b"schema_version"] = str(SCHEMA_VERSION).encode()
    meta[b"hashes_backfilled_at"] = io.now_kst().encode()
    pq.write_table(table.replace_schema_metadata(meta), path)
    return True, f"{table.num_rows:,}행에 content_sha256 을 채웠다 (벡터 무변경)"
