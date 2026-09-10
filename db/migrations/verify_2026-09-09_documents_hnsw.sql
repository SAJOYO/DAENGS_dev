-- verify: documents.embedding 의 HNSW 인덱스 (RAG-084 · D16 · #384)
--
-- **단언형이다** — 이유는 verify_2026-09-05_app_user_nickname.sql 머리말과 같다.
-- 사람이 눈으로 볼 질의는 단언 뒤에 남겼다.
--
-- ⚠️ **이 장은 `vector` 확장을 요구한다.** 하네스 CI 서비스가 `pgvector/pgvector:pg17` 인
-- 이유이고, verify_2026-09-01_training_rag_into_vectordb.sql 과 같다.
--
-- **이름만 보면 안 된다.** 이 인덱스가 틀리는 방식 셋은 전부 조용하다:
--   ⓐ 인덱스가 아예 없다        → 전수 스캔. 결과는 맞고 느리기만 하다
--   ⓑ 접근 방식이 hnsw 가 아니다 → ivfflat 으로 만들면 recall 특성이 다른데 이름은 같다
--   ⓒ 연산자 클래스가 다르다     → `<=>` 질의가 인덱스를 **안 탄다.** 결과는 여전히 맞다
-- 셋 다 예외를 안 내므로 여기서 카탈로그를 직접 읽는다.
--
-- `documents.embedding` 의 타입은 **따로 안 단언한다** — hnsw + vector_cosine_ops 인덱스는
-- vector 칸에만 설 수 있어서 ⓐⓑ 가 이미 그것을 뜻한다. 단언을 더해도 변조로 도달할 수
-- 없는 자리라(하네스가 못 재는 단언이 된다) 두지 않는다.
DO $verify$
DECLARE
    docs regclass;
    definition text;
    method text;
BEGIN
    docs := to_regclass('documents');
    IF docs IS NULL THEN
        RAISE EXCEPTION 'missing table: documents';
    END IF;

    -- ⓐ 있고, 유효한가. `indisvalid`·`indisready` 를 같이 보는 이유는 실패한
    -- `CREATE INDEX CONCURRENTLY` 가 **이름은 있는데 안 쓰이는** 인덱스를 남기기 때문이다.
    SELECT pg_get_indexdef(i.indexrelid), am.amname
      INTO definition, method
    FROM pg_index i
    JOIN pg_class c ON c.oid = i.indexrelid
    JOIN pg_am am ON am.oid = c.relam
    WHERE i.indrelid = docs AND c.relname = 'idx_documents_embedding'
      AND i.indisvalid AND i.indisready;
    IF definition IS NULL THEN
        RAISE EXCEPTION 'index mismatch: idx_documents_embedding missing or invalid';
    END IF;

    -- ⓑ 접근 방식
    IF method IS DISTINCT FROM 'hnsw' THEN
        RAISE EXCEPTION 'index mismatch: idx_documents_embedding must be hnsw, got %', method;
    END IF;

    -- ⓒ 연산자 클래스 — 검색이 `<=>` 로 묻는다. 다르면 인덱스를 안 타고 조용히 느려진다.
    IF position('vector_cosine_ops' IN definition) = 0 THEN
        RAISE EXCEPTION
            'index mismatch: idx_documents_embedding must use vector_cosine_ops, got %',
            definition;
    END IF;

END
$verify$;

-- 사람이 눈으로 볼 것 — 단언 뒤에 둔다.
SELECT c.relname AS index_name,
       am.amname AS method,
       pg_size_pretty(pg_relation_size(c.oid)) AS size,
       pg_get_indexdef(i.indexrelid) AS definition
FROM pg_index i
JOIN pg_class c ON c.oid = i.indexrelid
JOIN pg_am am ON am.oid = c.relam
WHERE i.indrelid = to_regclass('documents')
ORDER BY c.relname;
