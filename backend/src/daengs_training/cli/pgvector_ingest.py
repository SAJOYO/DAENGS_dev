"""Embed structure-preserving chunks and upsert them into local pgvector.

⚠ **충돌 대상은 `(document_id, chunk_index, embedding_model)` 이다 — `chunk_id` 가 아니다.**

2026-09-08 이전에는 `ON CONFLICT(chunk_id)` 였고 `chunk_id` 가 PK 였다. 그 조합은 다른 임베딩
모델로 재적재할 때 **옛 벡터를 덮어썼다** — `UNIQUE(document_id, chunk_index, embedding_model)`
이 공존을 허용하기 전에 PK 에서 먼저 충돌했기 때문이다. 예외도 경고도 안 났고 로그는 성공으로
찍혔다. 그래서 청킹·임베딩 실험의 대조군이 만들어지지 않았다.

한쪽만 모델을 보고 있었던 것이 원인이다 — 아래 중복 제거는 `where embedding_model=%s` 로
모델을 보는데 UPSERT 는 안 봤다. 검색(`retrieval/pgvector.py`)도 이미 모델로 거르고 있었으므로
깨져 있던 것은 이 한 줄이었다.

`chunk_id` 형식은 **안 바꾼다.** 그 값이 서빙 응답(`EvidenceCard`)과 평가 산출물의 id 로 나가
있어서, 바꾸면 기존 판정 파일·리포트가 안 맞는다. 대신 PK 를 복합키로 옮겼다
(`db/migrations/2026-09-08_training_rag_embedding_key.sql`).
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

MODEL = "intfloat/multilingual-e5-base"
DEFAULT_CHUNKS = Path("data/scratch/chunks_structure_v1")

def rows(directory: Path):
    for path in sorted(directory.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip(): yield json.loads(line)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--chunks",type=Path,default=DEFAULT_CHUNKS); ap.add_argument("--dsn",default="postgresql://postgres:postgres@localhost:5432/vectordb"); ap.add_argument("--model",default=MODEL); ap.add_argument("--embedding-label",default=None); ap.add_argument("--batch-size",type=int,default=64); ap.add_argument("--device",default=None); ap.add_argument("--limit",type=int,default=None); args=ap.parse_args()
    try:
        import psycopg
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise SystemExit("Install runtime deps: uv run --with 'psycopg[binary]' --with sentence-transformers ...") from exc
    label=args.embedding_label or args.model
    data=list(rows(args.chunks));
    if args.limit: data=data[:args.limit]
    with psycopg.connect(args.dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("select chunk_id from training_rag_chunks where embedding_model=%s", (label,))
            existing={row[0] for row in cur.fetchall()}
        data=[r for r in data if r["chunk_id"] not in existing]
        model=SentenceTransformer(args.model, device=args.device)
        inserted=updated=0
        for start in range(0, len(data), args.batch_size):
            batch=data[start:start+args.batch_size]
            vectors=model.encode(["passage: "+r["text"] for r in batch], batch_size=args.batch_size, normalize_embeddings=True, show_progress_bar=False)
            with conn.cursor() as cur:
              for r, vec in zip(batch, vectors):
                doc_id=r["doc_id"]; content_sha=hashlib.sha256(r["text"].encode()).hexdigest()
                token_count=int(r.get("token_count") or max(1,len(r["text"])//2))
                cur.execute("""INSERT INTO training_rag_documents(document_id,source_id,content_sha256,metadata) VALUES(%s,%s,%s,%s) ON CONFLICT(document_id) DO UPDATE SET content_sha256=EXCLUDED.content_sha256,metadata=EXCLUDED.metadata""",(doc_id,doc_id,content_sha,json.dumps({"heading_path":r.get("heading_path",[])})))
                # `RETURNING (xmax = 0)` 이 **새 행인지 갱신인지** 알려 준다. 이 구분을 로그에
                # 내는 것이 이 카드의 절반이다 — 옛 코드는 덮어쓰기를 "upserted" 한 마디로
                # 가려서, 벡터가 사라진 것을 아무도 몰랐다.
                cur.execute("""INSERT INTO training_rag_chunks(chunk_id,document_id,chunk_index,text,token_count,metadata,embedding_model,embedding,content_sha256) VALUES(%s,%s,%s,%s,%s,%s,%s,%s::vector,%s) ON CONFLICT(document_id,chunk_index,embedding_model) DO UPDATE SET chunk_id=EXCLUDED.chunk_id,text=EXCLUDED.text,token_count=EXCLUDED.token_count,metadata=EXCLUDED.metadata,embedding=EXCLUDED.embedding,content_sha256=EXCLUDED.content_sha256 RETURNING (xmax = 0) AS is_insert""",(r["chunk_id"],doc_id,r["chunk_index"],r["text"],token_count,json.dumps({"kinds":r.get("kinds",[]),"heading_path":r.get("heading_path",[])}),label,"["+",".join(str(float(x)) for x in vec)+"]",content_sha))
                if cur.fetchone()[0]: inserted+=1
                else: updated+=1
            conn.commit()
            print(json.dumps({"processed":min(start+len(batch),len(data)),"total":len(data)}), flush=True)
        with conn.cursor() as cur:
            cur.execute("select embedding_model, count(*) from training_rag_chunks group by 1 order by 1")
            models={row[0]: row[1] for row in cur.fetchall()}
    # **표에 실제로 무엇이 있는지 같이 찍는다.** 모델이 하나뿐인데 둘을 넣었다고 생각했다면
    # 그것이 바로 이 카드가 고친 실패다 — 숫자로 보이게 둔다.
    print(json.dumps({"chunks":len(data),"skipped_existing":len(existing),"inserted":inserted,"updated":updated,"model":args.model,"embedding_label":label,"models_in_table":models,"status":"upserted"},ensure_ascii=False))
if __name__=="__main__": main()
