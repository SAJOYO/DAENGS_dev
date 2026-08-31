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
`training-rag-pgvector` service is retired; Training now lives in the shared
`pgvector` container, on a database of its own:

```
shared `pgvector` container / PostgreSQL cluster
├─ `vectordb` — main DAENGS data
└─ `dog_rag` — Training RAG data
```

Sharing a PostgreSQL cluster does not mean Training tables are mixed into
`vectordb`. `rag_documents` and `rag_chunks` stay in the separate `dog_rag`
database, exactly as before. Training connects as its own `dog_rag` LOGIN
role, which is not a superuser and owns only that database; the main backend
keeps reaching `vectordb` through `DAENGS_DB_*` / `POSTGRES_*`. The two
connection paths do not overlap.

The retired container's old volume is deliberately kept as a rollback copy.
Backing it up and eventually removing it is an operational follow-up, not part
of application runtime.

The backend container receives `GEMINI_API_KEY`, `GEMINI_MODEL` (default
`gemini-3.1-flash-lite`), `GEMINI_TIMEOUT_MS`, and `RAG_PGVECTOR_DSN`, whose
password comes from `TRAINING_RAG_DB_PASSWORD` in the deployment root `.env`
via a required Compose interpolation. It uses the backend `ml` dependency
group and shared Hugging Face cache.

Offline ingestion is available from the backend environment via
`python -m daengs_training.cli.pgvector_ingest`; it is not part of request
serving and must only be run as an explicit data operation.

If measured production memory or latency becomes unacceptable, the boundary at
`daengs_backend.services.training_rag` can later be extracted into a service.
That extraction is intentionally not part of the current architecture.
