-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — 이유는 verify_2026-09-05_app_user_nickname.sql 머리말과 같다.
-- 사람이 눈으로 볼 질의는 단언 뒤에 남겼다.
--
-- ⚠️ **이 장은 `vector` 확장을 요구한다.** `training_rag_chunks.embedding` 이 `vector(768)`
-- 이고 인덱스가 `hnsw` 라, 확장 없는 Postgres 에서는 마이그레이션 자체가 안 돈다.
-- 하네스(`tools/check_migration_verification.py`)의 CI 서비스가 그래서 `pgvector/pgvector:pg17` 이다.
--
-- **차원이 768 인 것이 Life 의 1024 와 다르다** — 훈련 RAG 는 별도 임베딩 모델을 쓰고
-- 별도 표를 가진다. 이 둘을 섞으면 코사인이 무의미해지는데 **차원이 달라 예외는 난다**
-- (Life 쪽 `documents` 는 차원이 같아서 예외조차 안 나는 것과 반대다 — CLAUDE.md 참고).
DO $verify$
DECLARE
    item record;
    documents regclass;
    chunks regclass;
    definition text;
BEGIN
    IF to_regclass('training_rag_documents') IS NULL THEN
        RAISE EXCEPTION 'missing table: training_rag_documents';
    END IF;
    IF to_regclass('training_rag_chunks') IS NULL THEN
        RAISE EXCEPTION 'missing table: training_rag_chunks';
    END IF;
    documents := to_regclass('training_rag_documents');
    chunks := to_regclass('training_rag_chunks');

    FOR item IN SELECT * FROM (VALUES
        ('document_id', 'text', 'true'),
        ('source_id', 'text', 'true'),
        ('source_url', 'text', 'false'),
        ('content_sha256', 'text', 'true'),
        ('metadata', 'jsonb', 'true'),
        ('created_at', 'timestamp with time zone', 'true')
    ) AS expected(column_name, type_name, required) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = documents AND a.attname = item.column_name
              AND a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) = item.type_name
              AND a.attnotnull = item.required::boolean
        ) THEN
            RAISE EXCEPTION 'column mismatch: training_rag_documents.% (type %, not null %)',
                item.column_name, item.type_name, item.required;
        END IF;
    END LOOP;

    FOR item IN SELECT * FROM (VALUES
        ('chunk_id', 'text', 'true'),
        ('document_id', 'text', 'true'),
        ('chunk_index', 'integer', 'true'),
        ('text', 'text', 'true'),
        ('token_count', 'integer', 'true'),
        ('metadata', 'jsonb', 'true'),
        -- 어느 모델로 만든 벡터인가. **UNIQUE 의 일부이기도 하다** — 모델을 바꿔 다시
        -- 적재해도 옛 벡터와 공존할 수 있어야 하기 때문이다.
        ('embedding_model', 'text', 'true'),
        -- **차원까지 단언한다.** `vector` 로만 두면 아무 차원이나 들어오고, 그때 검색이
        -- 런타임에 죽는다. 768 은 훈련 RAG 의 모델 차원이다.
        ('embedding', 'vector(768)', 'true'),
        ('content_sha256', 'text', 'true'),
        ('created_at', 'timestamp with time zone', 'true')
    ) AS expected(column_name, type_name, required) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = chunks AND a.attname = item.column_name
              AND a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) = item.type_name
              AND a.attnotnull = item.required::boolean
        ) THEN
            RAISE EXCEPTION 'column mismatch: training_rag_chunks.% (type %, not null %)',
                item.column_name, item.type_name, item.required;
        END IF;
    END LOOP;

    -- PK 둘 · FK 하나 · UNIQUE 하나.
    --
    -- FK 는 **CASCADE 여야 한다** — 문서를 지웠는데 청크가 남으면 어느 문서의 것인지
    -- 모르는 벡터가 검색에 계속 잡힌다. 그 답은 출처를 못 대는 답이 된다.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        WHERE c.conrelid = documents AND c.contype = 'p' AND c.convalidated
          AND ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(num, pos)
                    JOIN pg_attribute a ON a.attrelid = documents AND a.attnum = k.num
                    ORDER BY k.pos) = ARRAY['document_id']
    ) THEN
        RAISE EXCEPTION 'constraint mismatch: training_rag_documents pk on document_id';
    END IF;

    FOR item IN SELECT * FROM (VALUES
        ('p', 'chunk_id', NULL, NULL),
        ('f', 'document_id', 'training_rag_documents', 'c'),
        -- 같은 문서·같은 순번이라도 **모델이 다르면 다른 행**이다.
        ('u', 'document_id,chunk_index,embedding_model', NULL, NULL)
    ) AS expected(kind, columns, target_table, delete_action) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint c
            WHERE c.conrelid = chunks AND c.contype::text = item.kind
              AND c.convalidated
              AND ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(num, pos)
                        JOIN pg_attribute a ON a.attrelid = chunks AND a.attnum = k.num
                        ORDER BY k.pos) = string_to_array(item.columns, ',')
              AND (item.kind <> 'f' OR (
                  c.confrelid = to_regclass(item.target_table)
                  AND c.confdeltype::text = item.delete_action
              ))
              AND (item.kind NOT IN ('p', 'u') OR EXISTS (
                  SELECT 1 FROM pg_index i WHERE i.indexrelid = c.conindid
                    AND i.indisvalid AND i.indisready AND i.indisunique
              ))
        ) THEN
            RAISE EXCEPTION 'constraint mismatch: training_rag_chunks kind % columns %',
                item.kind, item.columns;
        END IF;
    END LOOP;

    -- **HNSW 인덱스가 있어야 한다 — 여기는 Life 쪽과 다르다.**
    -- Life 의 `documents` 는 `db/indexes.sql` 이 "적재 뒤 수동"으로 미뤄 두었고 실제로
    -- 아직 안 만들어져 있다(전수 스캔). 훈련 RAG 는 이 마이그레이션이 직접 만든다.
    -- `vector_cosine_ops` 여야 하는 것도 중요하다 — 다른 연산자로 만들면 `<=>` 질의가
    -- **인덱스를 안 타는데 결과는 맞게 나온다.** 느려질 뿐이라 아무도 안 알려준다.
    SELECT pg_get_indexdef(i.indexrelid) INTO definition
    FROM pg_index i
    JOIN pg_class c ON c.oid = i.indexrelid
    WHERE i.indrelid = chunks AND c.relname = 'training_rag_chunks_embedding_hnsw'
      AND i.indisvalid AND i.indisready;
    IF definition IS NULL THEN
        RAISE EXCEPTION
            'index mismatch: training_rag_chunks_embedding_hnsw missing or invalid';
    END IF;
    IF position('hnsw' IN definition) = 0
       OR position('vector_cosine_ops' IN definition) = 0 THEN
        RAISE EXCEPTION 'index mismatch: embedding index must be hnsw + vector_cosine_ops, got %',
            definition;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_index i
        JOIN pg_class c ON c.oid = i.indexrelid
        WHERE i.indrelid = chunks AND c.relname = 'training_rag_chunks_document_idx'
          AND i.indisvalid AND i.indisready
    ) THEN
        RAISE EXCEPTION 'index mismatch: training_rag_chunks_document_idx missing or invalid';
    END IF;
END
$verify$;

-- ─────────────────────────────────────────────────────────────────────────
-- 사람이 눈으로 보는 자리. 위 단언이 통과한 뒤에만 여기까지 온다.
-- ─────────────────────────────────────────────────────────────────────────

-- 문서·청크 수와 쓰인 모델. 모델이 둘 이상이면 옛 벡터가 같이 있는 것이고,
-- 그 자체는 정상이다 (UNIQUE 가 공존을 허용한다).
SELECT (SELECT count(*) FROM training_rag_documents) AS documents,
       (SELECT count(*) FROM training_rag_chunks) AS chunks,
       (SELECT count(DISTINCT embedding_model) FROM training_rag_chunks) AS models;

-- 부모 없는 청크. **0이어야 한다** (FK 가 막지만 눈으로도 본다).
SELECT count(*) AS orphan_chunks
FROM training_rag_chunks c
LEFT JOIN training_rag_documents d ON d.document_id = c.document_id
WHERE d.document_id IS NULL;
