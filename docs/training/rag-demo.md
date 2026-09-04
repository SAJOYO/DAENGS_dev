# Training RAG modular monolith

The public contract remains `POST /training/chat` on the main DAENGS backend.
The router calls `daengs_training` in-process; there is no internal HTTP service.

Runtime code and reviewed resources live under `backend/src/daengs_training/`.
The blocking E5 retrieval, synchronous PGVector access, and Gemini request run
on a worker thread. The runtime is initialized lazily on the first Training
request, so importing or starting the backend does not load SentenceTransformer.

The approved serving manifest remains 14 documents / 83 database chunks.
Do not re-ingest or re-embed as part of deployment.

## Database topology

Training no longer runs a PostgreSQL container of its own. The standalone
`training-rag-pgvector` service is retired, and the separate `dog_rag` database
it was replaced by is retired too (#112). Training now shares the main database:

```
shared `pgvector` container / PostgreSQL cluster
└─ `vectordb`
   ├─ (main DAENGS tables)
   └─ public.training_rag_documents · public.training_rag_chunks   (Training-owned, #112)
```

Training and the backend use **the same database and the same credentials**;
they are separated by table ownership, not by database or role. There is no
`dog_rag` LOGIN role and no `TRAINING_RAG_DB_PASSWORD` any more —
`RAG_PGVECTOR_DSN` is assembled by Compose from the main `POSTGRES_*` values.
The authoritative description is `docs/orchestration/architecture.md`,
"DB 토폴로지 (#112)".

The retired `dog_rag` database may still exist on a running server as a
rollback copy, but it is no longer a production runtime target. **`db/init/`
only runs on an empty volume**, so an already-running server needs
`db/migrations/2026-09-01_training_rag_into_vectordb.sql` applied by hand.

The backend container receives `GEMINI_API_KEY`, `GEMINI_MODEL` (default
`gemini-3.1-flash-lite`), `GEMINI_TIMEOUT_MS`, and `RAG_PGVECTOR_DSN`. It uses
the backend `ml` dependency group and shared Hugging Face cache.

Offline ingestion is available from the backend environment via
`python -m daengs_training.cli.pgvector_ingest`; it is not part of request
serving and must only be run as an explicit data operation.

If measured production memory or latency becomes unacceptable, the boundary at
`daengs_backend.services.training_rag` can later be extracted into a service.
That extraction is intentionally not part of the current architecture.
