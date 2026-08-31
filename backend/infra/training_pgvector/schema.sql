CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS rag_documents (
  document_id text PRIMARY KEY,
  source_id text NOT NULL,
  source_url text,
  content_sha256 text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rag_chunks (
  chunk_id text PRIMARY KEY,
  document_id text NOT NULL REFERENCES rag_documents(document_id) ON DELETE CASCADE,
  chunk_index integer NOT NULL,
  text text NOT NULL,
  token_count integer NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  embedding_model text NOT NULL,
  embedding vector(768) NOT NULL,
  content_sha256 text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(document_id, chunk_index, embedding_model)
);

CREATE INDEX IF NOT EXISTS rag_chunks_embedding_hnsw
  ON rag_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS rag_chunks_document_idx ON rag_chunks(document_id);
