"""Embed structure-preserving chunks and upsert them into local pgvector."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

MODEL = "intfloat/multilingual-e5-base"
DEFAULT_CHUNKS = Path("data/scratch/chunks_structure_v1")

def rows(directory: Path):
    for path in sorted(directory.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip(): yield json.loads(line)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--chunks",type=Path,default=DEFAULT_CHUNKS); ap.add_argument("--dsn",default="postgresql://dog_rag:dog_rag_local@localhost:5433/dog_rag"); ap.add_argument("--model",default=MODEL); ap.add_argument("--embedding-label",default=None); ap.add_argument("--batch-size",type=int,default=64); ap.add_argument("--device",default=None); ap.add_argument("--limit",type=int,default=None); args=ap.parse_args()
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
            cur.execute("select chunk_id from rag_chunks where embedding_model=%s", (label,))
            existing={row[0] for row in cur.fetchall()}
        data=[r for r in data if r["chunk_id"] not in existing]
        model=SentenceTransformer(args.model, device=args.device)
        for start in range(0, len(data), args.batch_size):
            batch=data[start:start+args.batch_size]
            vectors=model.encode(["passage: "+r["text"] for r in batch], batch_size=args.batch_size, normalize_embeddings=True, show_progress_bar=False)
            with conn.cursor() as cur:
              for r, vec in zip(batch, vectors):
                doc_id=r["doc_id"]; content_sha=hashlib.sha256(r["text"].encode()).hexdigest()
                token_count=int(r.get("token_count") or max(1,len(r["text"])//2))
                cur.execute("""INSERT INTO rag_documents(document_id,source_id,content_sha256,metadata) VALUES(%s,%s,%s,%s) ON CONFLICT(document_id) DO UPDATE SET content_sha256=EXCLUDED.content_sha256,metadata=EXCLUDED.metadata""",(doc_id,doc_id,content_sha,json.dumps({"heading_path":r.get("heading_path",[])})))
                cur.execute("""INSERT INTO rag_chunks(chunk_id,document_id,chunk_index,text,token_count,metadata,embedding_model,embedding,content_sha256) VALUES(%s,%s,%s,%s,%s,%s,%s,%s::vector,%s) ON CONFLICT(chunk_id) DO UPDATE SET text=EXCLUDED.text,token_count=EXCLUDED.token_count,metadata=EXCLUDED.metadata,embedding_model=EXCLUDED.embedding_model,embedding=EXCLUDED.embedding,content_sha256=EXCLUDED.content_sha256""",(r["chunk_id"],doc_id,r["chunk_index"],r["text"],token_count,json.dumps({"kinds":r.get("kinds",[]),"heading_path":r.get("heading_path",[])}),label,"["+",".join(str(float(x)) for x in vec)+"]",content_sha))
            conn.commit()
            print(json.dumps({"processed":min(start+len(batch),len(data)),"total":len(data)}), flush=True)
    print(json.dumps({"chunks":len(data),"skipped_existing":len(existing),"model":args.model,"embedding_label":label,"status":"upserted"}))
if __name__=="__main__": main()
