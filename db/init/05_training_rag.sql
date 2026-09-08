CREATE TABLE IF NOT EXISTS training_rag_documents (
  document_id text PRIMARY KEY,
  source_id text NOT NULL,
  source_url text,
  content_sha256 text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- 한 청킹의 **여러 임베딩이 나란히** 산다. 그것이 키를 이렇게 잡은 이유다.
--
-- 청킹이나 임베딩 모델을 바꿔 비교하려면 옛 벡터와 새 벡터가 같이 있어야 하고, 검색은
-- `embedding_model` 로 걸러 한 쪽만 본다 (`retrieval/pgvector.py` 의 `where embedding_model=%s`).
--
-- ⚠ **`chunk_id` 는 PK 가 아니다.** 2026-09-08 이전에는 PK 였고, 그 탓에 다른 모델로 재적재하면
--   UNIQUE 가 판단할 기회 없이 PK 에서 먼저 충돌해 **옛 벡터를 덮어썼다** — 예외도 경고도 없이.
--   `chunk_id` 를 컬럼으로 남긴 것은 그 값이 서빙 응답(`EvidenceCard`)과 평가 산출물의 id 로
--   나가서, 형식을 바꾸면 기존 판정 파일·리포트가 안 맞기 때문이다.
--   이미 도는 DB 는 `db/migrations/2026-09-08_training_rag_embedding_key.sql` 로 옮긴다.
CREATE TABLE IF NOT EXISTS training_rag_chunks (
  chunk_id text NOT NULL,
  document_id text NOT NULL REFERENCES training_rag_documents(document_id) ON DELETE CASCADE,
  chunk_index integer NOT NULL,
  text text NOT NULL,
  token_count integer NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  embedding_model text NOT NULL,
  embedding vector(768) NOT NULL,
  content_sha256 text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  -- 한 (문서, 청크번호)는 모델마다 한 행. 적재의 ON CONFLICT 대상이 바로 이것이다.
  PRIMARY KEY (document_id, chunk_index, embedding_model),
  -- **소비자가 기대하는 불변식**: 한 모델 안에서 `chunk_id` 는 유일하다. 검색이 늘 한 모델로
  -- 거르므로 결과 안에서는 여전히 id 하나가 청크 하나를 가리킨다.
  UNIQUE (chunk_id, embedding_model)
);

CREATE INDEX IF NOT EXISTS training_rag_chunks_embedding_hnsw
  ON training_rag_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS training_rag_chunks_document_idx
  ON training_rag_chunks(document_id);
