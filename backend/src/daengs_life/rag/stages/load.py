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

import collections
from dataclasses import dataclass, field
from typing import Any

from ..core import config, io, tokenize
from . import embed
from .chunk import content_hash

VERSION = 1

# 컬럼으로 가는 것과 metadata 로 가는 것 (RAG-008 표준 키 + RAG-025 ④).
# **한 곳에만 적는다** — 두 목록이 갈리면 "이 값이 왜 metadata 에 없지"를 두 파일에서 찾게 된다.
#
# ⚠️ 아래 INSERT 문이 이 튜플로 조립된다. 예전에는 SQL 에 컬럼을 손으로 한 번 더 적었는데,
# `content_tokens` 를 더할 때 이 튜플만 고치고 SQL 을 안 고쳐 **토큰이 전부 빈 채로
# 적재가 성공했다** (RAG-035 에서 실제로 겪었다). 목록을 하나로 만들어 그 실수를 없앴다.
COLUMNS = ("content", "content_hash", "embedding", "content_tokens", "category", "subcategory",
           "source", "source_type", "source_url", "document_title", "section", "metadata")

# metadata 로 옮기는 청크 필드. 표준 키(raw_file·format·trust_level·published_at·license)와
# 우리 키(chunk_id·citation·citation_url·element_type·chars·doc_id·source_id)를 나눠 적지 않는다 —
# 어차피 같은 JSONB 한 덩어리이고, 표준/비표준의 구분은 스키마 주석이 이미 갖고 있다.
META_FIELDS = ("raw_file", "format", "trust_level", "published_at", "license",
               "chunk_id", "citation", "citation_url", "element_type", "chars",
               "doc_id", "source_id", "part",
               "org")   # 지자체·소관기관명 — 지역 필터가 이것으로 거른다 (RAG-063)


@dataclass
class Prepared:
    """적재 직전의 행들 + 무엇이 합쳐졌나."""
    rows: list[dict[str, Any]] = field(default_factory=list)
    merged: int = 0                       # content_hash 충돌로 합쳐진 청크 수
    model_repo: str = ""


@dataclass
class Written:
    """`upsert()` 가 **실제로** 무엇을 했나 (RAG-084 ④).

    셋을 가르지 않으면 화면에 `upserted 9844행` 한 줄만 남는데, 그 줄은 증분화가 도는 날과
    안 도는 날에 똑같이 찍힌다. `unchanged` 가 이 카드의 성공 지표다.
    """
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0

    @property
    def touched(self) -> int:
        """실제로 새 튜플이 생긴 행 = 인덱스가 갱신된 행."""
        return self.inserted + self.updated


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
            # 렉시컬 축 (RAG-003). **임베딩과 같은 자리에서 만든다** — 둘이 다른 단계로
            # 갈라지면 한쪽만 채워진 행이 생기고, 그 상태는 dense 로는 안 보인다.
            "content_tokens": tokenize.tokenized(chunk["content"]),
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
INSERT INTO documents ({cols})
VALUES ({vals})
ON CONFLICT (content_hash) DO UPDATE SET
    embedding      = EXCLUDED.embedding,
    -- 토큰은 **갱신한다.** 토큰화 규칙(`core/tokenize.py`)을 고치면 `content` 는 그대로인데
    -- 토큰만 바뀌는데, 갱신 목록에 없으면 옛 토큰이 남는다 — `embedding` 을 갱신하는 것과
    -- 똑같은 이유이고, 증상도 똑같이 조용하다 (RAG-035)
    content_tokens = EXCLUDED.content_tokens,
    metadata       = EXCLUDED.metadata
WHERE documents.embedding      IS DISTINCT FROM EXCLUDED.embedding
   OR documents.content_tokens IS DISTINCT FROM EXCLUDED.content_tokens
   OR documents.metadata       IS DISTINCT FROM EXCLUDED.metadata
""".format(cols=", ".join(COLUMNS), vals=", ".join(f"%({c})s" for c in COLUMNS))


def upsert(conn, rows: list[dict[str, Any]], batch: int = 500) -> Written:
    """**한 트랜잭션**으로 전부 넣는다 (RAG-025 ①). 안 바뀐 행은 **쓰지 않는다** (RAG-084).

    `content` 와 나머지 컬럼은 갱신하지 않는다 — `content_hash` 가 `content` 의 해시라 정의상
    같고, 다른 컬럼은 같은 청크에서 나온 같은 값이다. 바뀌는 것은 `embedding` 과 `metadata`
    (`embedding_model` 이 그 안에 있다) 뿐이다. `updated_at` 은 트리거가 맡는다.

    **`UPSERT` 의 `WHERE` 가 증분화의 전부다** (RAG-084 ②). 조건이 없으면 값이 하나도 안
    달라져도 `DO UPDATE` 가 돌아 전 행에 새 튜플 버전이 생기고, 그 수만큼 **테이블의 모든
    인덱스**에 항목이 들어간다. 2026-09-09 실측: 9,844행 중 실제로 달라진 것 **0행**인데
    upsert 는 9.22초를 쓰고 인덱스 넷을 통째로 갈았다. HNSW 를 켜면 그 자리에 제일 비싼
    다섯 번째가 붙는다 — `D16` 이 증분화와 인덱스를 **한 카드로 묶은 이유**가 이것이다.

    판별은 **DB 가 한다.** 클라이언트가 "안 바뀌었을 것"이라고 미리 거르지 않는 이유는,
    그 판단이 틀리면 행이 **조용히 낡은 채로 남기** 때문이다 — `metadata` 를 통째로 갈아
    끼우는 이 upsert 에서 그 실수는 `org` 2,592행이 사라진 사고(RAG-066 ①)와 같은 모양이 된다.
    `IS DISTINCT FROM` 은 세 값을 서버에서 직접 비교하므로 틀릴 여지가 없다. `metadata` 는
    JSONB 라 키 순서·공백이 정규화된 뒤에 비교되고, `embedding` 은 pgvector 의 `=` 를 탄다.

    돌려주는 `Written` 이 **화면에 나오는 수**다. 없으면 증분화가 됐는지 안 됐는지 알 길이
    없다 — 성공한 적재는 어느 쪽이든 똑같이 조용하다.
    """
    from psycopg.types.json import Jsonb

    payload = [{**r, "metadata": Jsonb(r["metadata"])} for r in rows]
    with conn.transaction():                       # 중간에 죽으면 통째로 되돌린다 = 모델 혼입 불가
        with conn.cursor() as cur:
            # 신규와 갱신을 가르려고 **먼저** 읽는다. 같은 트랜잭션 안이라 그사이 바뀌지 않는다.
            cur.execute("SELECT content_hash FROM documents")
            known = {h for (h,) in cur}
            touched = 0
            for i in range(0, len(payload), batch):
                cur.executemany(UPSERT, payload[i:i + batch])
                touched += cur.rowcount            # `WHERE` 가 막은 행은 0으로 센다
    inserted = sum(1 for r in rows if r["content_hash"] not in known)
    return Written(inserted=inserted, updated=touched - inserted,
                   unchanged=len(rows) - touched)


def metadata_loss(conn, rows: list[dict[str, Any]]) -> list[tuple[str, int, int]]:
    """**이번 적재가 DB 에서 지워 버릴 메타 키.** `(키, DB 행 수, 이번 행 수)` 로 돌려준다.

    ────────────────────────────────────────────────────────────────────
    왜 있나 — 2026-09-06 에 지역 필터가 통째로 죽었다 (RAG-066 ①)
    ────────────────────────────────────────────────────────────────────
    `UPSERT` 는 `metadata` 를 **통째로 갈아끼운다**(병합이 아니다). 그래서 청크에 없는 키는
    DB 에서 그냥 사라진다. `org`(RAG-063)이 마이그레이션으로 DB 에만 있고 08-29 자 청크
    파일에는 없던 상태에서 `rag load` 가 한 번 돌자 2,592행의 `org` 이 전부 지워졌고,
    **적재는 성공하고 스모크도 통과하고 예외도 안 났다.** 랩을 두 번 돌 때까지 몰랐다.

    판정은 **"있던 것이 통째로 사라지는가"** 하나다. 일부가 줄어드는 것은 정상일 수 있어
    (소스를 뺐다거나) 막지 않는다 — 조용히 전부 사라지는 것만 잡는다. 그 한 줄이 이 사고를
    막았을 것이고, 규칙이 좁아야 사람이 `--force` 를 반사적으로 붙이지 않는다.

    ⚠ **`META_FIELDS` 밖의 키도 본다.** 마이그레이션이 넣은 키는 정의상 `META_FIELDS` 에
    없을 수 있고(그것이 `org` 이 그렇게 살던 이유다), 그런 키야말로 여기서 잡혀야 한다.
    """
    have = collections.Counter()
    for r in rows:
        have.update(r["metadata"].keys())
    with conn.cursor() as cur:
        cur.execute("SELECT key, count(*) FROM documents, jsonb_each(metadata) GROUP BY key")
        db = dict(cur.fetchall())
    return sorted((k, n, have.get(k, 0)) for k, n in db.items() if n and not have.get(k))


def stale(conn, rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """**이번 적재가 안 건드린 행.** `(content_hash, chunk_id)` 로 돌려준다 (RAG-045 ①).

    `upsert` 는 `content_hash` 로 `ON CONFLICT DO UPDATE` 할 뿐 **사라진 청크를 지우지 않는다.**
    문서가 개정돼 청크가 없어지면 옛 행이 인덱스에 그대로 남고, 검색은 더 이상 나오면 안 되는
    것을 계속 후보로 본다. 에러가 안 나서 아무도 모른다 — 2026-08-28 에 118행, 08-29 에 26행이
    그렇게 남았고 둘 다 사람이 우연히 발견했다.

    **`content_hash` 로 비교하는 것이 핵심이다.** `chunk_id` 에는 수집일이 박혀 있어(RAG-022 ⑥B)
    재수집하면 주소가 전부 바뀌지만, 해시는 `content` 만 보므로 내용이 같으면 그대로다. `doc_id`
    로 비교하면 재수집 때마다 전량 삭제·전량 삽입이 된다.

    `prepare()` 가 코퍼스 전체를 만들기 때문에(`load` 에 소스 단위 옵션이 없다) 이 집합 차이가
    곧 "사라진 청크"다. **소스 단위 적재가 생기면 이 전제가 깨지므로** 그때 범위를 함께 받아야 한다.
    """
    keep = {r["content_hash"] for r in rows}
    with conn.cursor() as cur:
        cur.execute("SELECT content_hash, metadata->>'chunk_id' FROM documents")
        return [(h, cid) for h, cid in cur.fetchall() if h not in keep]


def prune(conn, targets: list[tuple[str, str]], batch: int = 500) -> int:
    """`stale()` 이 찾은 행을 지운다. **명시적으로 부를 때만 지운다** (RAG-045 ①).

    기본 동작을 삭제로 두지 않은 이유는 하나다 — 적재가 절반만 준비된 상태에서 돌면
    `stale()` 이 코퍼스 전체를 "사라졌다"고 볼 수 있고, 그 사고는 되돌릴 수 없다.
    """
    if not targets:
        return 0
    hashes = [h for h, _ in targets]
    with conn.transaction(), conn.cursor() as cur:
        for i in range(0, len(hashes), batch):
            cur.execute("DELETE FROM documents WHERE content_hash = ANY(%s)", (hashes[i:i + batch],))
    return len(hashes)


def count(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM documents")
        return cur.fetchone()[0]
