# Training RAG modular monolith

The public contract remains `POST /training/chat` on the main DAENGS backend.
The router calls `daengs_training` in-process; there is no internal HTTP service.

Runtime code and reviewed resources live under `backend/src/daengs_training/`.
The blocking E5 retrieval, synchronous PGVector access, and Gemini request run
on a worker thread. The runtime is initialized lazily on the first Training
request, so importing or starting the backend does not load SentenceTransformer.

The approved serving manifest remains 14 documents / 83 database chunks.
Training continues to use the isolated `training-rag-pgvector` service and the
`training-rag-pgdata` volume. Do not re-ingest or re-embed as part of deployment.

The backend container receives `GEMINI_API_KEY`, `GEMINI_MODEL` (default
`gemini-3.1-flash-lite`), `GEMINI_TIMEOUT_MS`, and `RAG_PGVECTOR_DSN`. It uses
the backend `ml` dependency group and shared Hugging Face cache.

Offline ingestion is available from the backend environment via
`python -m daengs_training.cli.pgvector_ingest`; it is not part of request
serving and must only be run as an explicit data operation.

If measured production memory or latency becomes unacceptable, the boundary at
`daengs_backend.services.training_rag` can later be extracted into a service.
That extraction is intentionally not part of the current architecture.
