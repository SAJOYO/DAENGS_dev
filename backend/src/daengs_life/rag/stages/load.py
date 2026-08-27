"""7단계 적재 — `chunks/` + `embeddings/{key}.parquet` → `documents` (RAG-008, RAG-025).

**테이블은 이미 RAG-008 이 만들었다**(`db/init/01_schema.sql`). 이 단계는 그 테이블을 채운다.
인덱스는 여기서 만들지 않는다 — `db/indexes.sql` 이 "적재가 끝난 뒤 수동"으로 못 박아 두었고,
빈 테이블에 HNSW 를 미리 만들면 INSERT 마다 그래프를 갱신하느라 느려진다.

**이 파일의 두 축은 RAG-025 ①이다.**

① **`ON CONFLICT DO UPDATE` + 전체 한 트랜잭션.** 스키마 주석이 적어 둔 `DO NOTHING` 은 모델을
   교체할 계획이 없을 때 쓰인 것이다. 모델을 바꿔도 `content` 는 안 바뀌므로 `content_hash` 도
   같고, `DO NOTHING` 이면 **1,407행 전부를 조용히 건너뛴 채 옛 벡터가 남는다.** 그러면 RAG-024
   `판정 이후` 가 세운 "교체는 같은 명령 재실행"이 거짓이 된다.
② **한 트랜잭션인 이유는 원자성이 곧 RAG-002 의 집행 장치**라서다. upsert 는 행 단위 갱신이라
   중간에 죽으면 `embedding` 한 컬럼에 두 모델의 벡터가 섞인다 — RAG-002 가 금지한 그 상태를
   중간 결과로 만든다. 1,407행이면 한 트랜잭션이 부담도 아니다.

**적재 모델은 인자다.** 지금 기본값은 판정 승자(`qwen3-embedding-0.6b`)가 아니라 기준선
`bge-m3` 인데, 그것이 RAG-024 `판정 이후` 의 결정이다 — 첫 관통에서 재는 것은 검색 품질이 아니라
배선이고, 비대칭 모델은 문제가 났을 때 원인을 둘로 만든다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core import config, io
from . import embed
from .chunk import content_hash

VERSION = 1

# 컬럼으로 가는 것과 metadata 로 가는 것 (RAG-008 표준 키 + RAG-025 ④).
# **한 곳에만 적는다** — 두 목록이 갈리면 "이 값이 왜 metadata 에 없지"를 두 파일에서 찾게 된다.
COLUMNS = ("content", "content_hash", "embedding", "category", "subcategory", "source",
           "source_type", "source_url", "document_title", "section", "metadata")

# metadata 로 옮기는 청크 필드. 표준 키(raw_file·format·trust_level·published_at·license)와
# 우리 키(chunk_id·citation·citation_url·element_type·chars·doc_id·source_id)를 나눠 적지 않는다 —
# 어차피 같은 JSONB 한 덩어리이고, 표준/비표준의 구분은 스키마 주석이 이미 갖고 있다.
META_FIELDS = ("raw_file", "format", "trust_level", "published_at", "license",
               "chunk_id", "citation", "citation_url", "element_type", "chars",
               "doc_id", "source_id", "part")


@dataclass
class Prepared:
    """적재 직전의 행들 + 무엇이 합쳐졌나."""
    rows: list[dict[str, Any]] = field(default_factory=list)
    merged: int = 0                       # content_hash 충돌로 합쳐진 청크 수
    model_repo: str = ""


def prepare(model_key: str) -> Prepared:
    """청크와 벡터를 맞물려 `documents` 행으로 만든다. **DB 를 건드리지 않는다.**

    분리해 둔 이유는 `--dry-run` 이 여기까지만 돌면 되기 때문이고, 테스트가 DB 없이 이 계약을
    검사할 수 있어야 하기 때문이다 (`parse_doc` 이 쓰기를 분리한 것과 같은 이유).

    **중복 처리 (RAG-025 ③)** — `content_hash` 가 같은 청크는 한 행으로 합친다. 실물 5쌍은 전부
    같은 별표의 두 행인데 구분해 줄 `근거 법조문` 칸이 비어 있어 **실제로 구별되지 않는다**.
    합치는 것 자체는 옳고, 문제는 조용한 것이다 — 대표 `chunk_id` 는 단수로 두되(골든셋 대조·
    8단계 검색이 단수를 전제한다) 사라지는 주소를 `metadata.merged_from` 에 남긴다.
    """
    import numpy as np
    import pyarrow.parquet as pq

    model = embed.MODELS[model_key]
    chunks = embed.load_chunks()
    path = embed.parquet_path(model_key)
    if not path.is_file():
        raise FileNotFoundError(f"{path.name} 이 없다 — `python -m rag embed --model {model_key}` 먼저")

    table = pq.read_table(path)
    ids = table["chunk_id"].to_pylist()
    if ids != [c["chunk_id"] for c in chunks]:
        # 4단계가 행 순서를 보존하므로 정상 경로에서는 일어나지 않는다. 어긋나면 **엉뚱한 청크에
        # 엉뚱한 벡터**가 붙고, 증상은 8단계 검색이 이상하다는 것으로만 나타난다
        raise ValueError(f"{path.name} 의 chunk_id 순서가 chunks/ 와 다르다 — 다시 임베딩할 것")
    vectors = np.stack(table["embedding"].to_pylist()).astype("float32")

    out = Prepared(model_repo=model.repo)
    by_hash: dict[str, dict[str, Any]] = {}
    for chunk, vector in zip(chunks, vectors):
        h = content_hash(chunk["content"])
        if h in by_hash:
            by_hash[h]["metadata"]["merged_from"].append(chunk["chunk_id"])
            out.merged += 1
            continue
        meta = {k: chunk[k] for k in META_FIELDS if chunk.get(k) not in (None, "")}
        meta["embedding_model"] = model.repo      # **실제로 쓴 모델** (RAG-024 판정 이후)
        meta["embedding_model_key"] = model_key
        meta["merged_from"] = []
        row = {
            "content": chunk["content"],
            "content_hash": h,
            "embedding": vector,
            "category": chunk.get("category") or "",
            "subcategory": chunk.get("subcategory") or "",
            "source": chunk.get("source") or None,
            "source_type": chunk.get("source_type") or None,
            "source_url": chunk.get("source_url"),
            "document_title": chunk.get("document_title") or None,
            "section": chunk.get("section"),
            "metadata": meta,
        }
        by_hash[h] = row
        out.rows.append(row)
    return out


# ---------------------------------------------------------------- DB
# DB 가 안 떠 있을 때 **얼마나 빨리 포기하는가.** 없으면 libpq 기본값(OS TCP 타임아웃)까지
# 통째로 문다 — `_conn_or_skip()` 은 실패한 **뒤에야** skip 하므로, DB 없이 pytest 를 돌리면
# DB 테스트 수만큼 그 시간이 곱해져 "멈춘 것처럼" 보인다. 실측(2026-08-27, 같은 PC):
# DB 기동 35초 / 미기동 test_load.py 부근에서 사실상 정지. **이 레포는 DB 가 원격(서버 PC)이라
# 더 자주 겪는다** — LAN 안이면 3초로 충분하고, 붙을 DB 가 멀면 이 한 줄을 올리면 된다.
CONNECT_TIMEOUT_SEC = 3


def connect():
    """psycopg3 연결 + pgvector 어댑터 등록 (RAG-025 ②).

    SQL 을 직접 쓴다 — 스키마의 단일 소스를 `db/init/01_schema.sql` 하나로 두기 위해서다.
    그 파일의 값은 SQL 이 아니라 **주석**에 있고(왜 `NOT NULL` 인지, 왜 그 CHECK 인지),
    ORM 모델을 두면 그 판단과 어긋날 수 있는 두 번째 주장이 생긴다.
    """
    import psycopg
    from pgvector.psycopg import register_vector

    conn = psycopg.connect(config.settings.dsn, connect_timeout=CONNECT_TIMEOUT_SEC)
    register_vector(conn)
    return conn


def existing_models(conn) -> list[tuple[str, int]]:
    """지금 테이블에 들어 있는 (embedding_model, 행 수). 적재 전에 보여 준다 (RAG-025 ①).

    **막지 않는다** — 교체는 정상 경로다. 다만 조용하면 안 된다. `rag chunk` 가 중복 5건을
    경고로 드러낸 것과 같은 처리다.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COALESCE(metadata->>'embedding_model', '(없음)'), count(*)
            FROM documents GROUP BY 1 ORDER BY 2 DESC
        """)
        return [(m, n) for m, n in cur.fetchall()]


UPSERT = """
INSERT INTO documents (content, content_hash, embedding, category, subcategory, source,
                       source_type, source_url, document_title, section, metadata)
VALUES (%(content)s, %(content_hash)s, %(embedding)s, %(category)s, %(subcategory)s, %(source)s,
        %(source_type)s, %(source_url)s, %(document_title)s, %(section)s, %(metadata)s)
ON CONFLICT (content_hash) DO UPDATE SET
    embedding = EXCLUDED.embedding,
    metadata  = EXCLUDED.metadata
"""


def upsert(conn, rows: list[dict[str, Any]], batch: int = 500) -> None:
    """**한 트랜잭션**으로 전부 넣는다 (RAG-025 ①).

    `content` 와 나머지 컬럼은 갱신하지 않는다 — `content_hash` 가 `content` 의 해시라 정의상
    같고, 다른 컬럼은 같은 청크에서 나온 같은 값이다. 바뀌는 것은 `embedding` 과 `metadata`
    (`embedding_model` 이 그 안에 있다) 뿐이다. `updated_at` 은 트리거가 맡는다.
    """
    from psycopg.types.json import Jsonb

    payload = [{**r, "metadata": Jsonb(r["metadata"])} for r in rows]
    with conn.transaction():                       # 중간에 죽으면 통째로 되돌린다 = 모델 혼입 불가
        with conn.cursor() as cur:
            for i in range(0, len(payload), batch):
                cur.executemany(UPSERT, payload[i:i + batch])


def count(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM documents")
        return cur.fetchone()[0]
