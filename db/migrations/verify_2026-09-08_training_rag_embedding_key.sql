-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — `tools/check_migration_verification.py` 가 스키마를 일부러 망가뜨린 뒤
-- 이 파일이 그것을 잡는지 본다. `db-migrate.yml` 도 종료 코드로 성공을 판정하므로
-- SELECT 만 있으면 **틀려도 통과한다.** 사람이 눈으로 볼 질의는 단언 뒤에 남겼다.
--
-- ⚠ **실패 문구에 `mismatch` 나 `missing table` 이 들어가야 한다.** 하네스가 stderr 에서
-- 그 낱말로 "verifier 가 잡았다" 와 "변조 SQL 이 죽었다" 를 가른다.
--
-- **이 표에서 제일 중요한 단언은 ① 이다** — PK 가 `chunk_id` 로 되돌아가지 않았는지.
-- 되돌아가면 다른 임베딩 모델로 재적재할 때 **옛 벡터를 조용히 덮어쓴다.** 예외도 경고도
-- 안 나고 로그는 성공으로 찍히므로(2026-09-08 에 고친 그 버그), 카탈로그로 잡는 수밖에 없다.
DO $verify$
DECLARE
    relation regclass;
    pk_columns text;
    uq_exists boolean;
    chunk_id_notnull boolean;
BEGIN
    IF to_regclass('training_rag_chunks') IS NULL THEN
        RAISE EXCEPTION 'missing table: training_rag_chunks';
    END IF;
    relation := to_regclass('training_rag_chunks');

    -- ① PK 는 (document_id, chunk_index, embedding_model) 이어야 한다.
    --    이것이 한 청킹의 여러 임베딩을 공존하게 하는 유일한 장치다.
    SELECT string_agg(att.attname::text, ', ' ORDER BY k.ord) INTO pk_columns
    FROM pg_constraint con
    CROSS JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
    JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = k.attnum
    WHERE con.conrelid = relation AND con.contype = 'p';

    IF pk_columns IS NULL THEN
        RAISE EXCEPTION 'training_rag_chunks primary key mismatch: PK 가 없습니다';
    END IF;
    IF pk_columns <> 'document_id, chunk_index, embedding_model' THEN
        RAISE EXCEPTION
            'training_rag_chunks primary key mismatch: 기대 (document_id, chunk_index, embedding_model), 실제 (%). '
            'PK 가 chunk_id 로 되돌아가면 다른 임베딩 모델 적재가 옛 벡터를 조용히 덮어씁니다.',
            pk_columns;
    END IF;

    -- ② UNIQUE(chunk_id, embedding_model). 소비자가 기대하는 불변식이다 —
    --    검색이 한 모델로 거르므로 결과 안에서 id 하나가 청크 하나를 가리켜야 한다.
    SELECT EXISTS (
        SELECT 1 FROM pg_constraint con
        WHERE con.conrelid = relation
          AND con.contype = 'u'
          AND (SELECT array_agg(att.attname::text ORDER BY att.attname)
               FROM unnest(con.conkey) AS k(attnum)
               JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = k.attnum)
              = ARRAY['chunk_id', 'embedding_model']
    ) INTO uq_exists;

    IF NOT uq_exists THEN
        RAISE EXCEPTION
            'training_rag_chunks unique constraint mismatch: UNIQUE(chunk_id, embedding_model) 이 없습니다';
    END IF;

    -- ③ chunk_id 는 PK 가 아니게 됐으므로 NOT NULL 이 명시적으로 걸려 있어야 한다.
    --    빠지면 서빙 응답(EvidenceCard)의 id 가 NULL 로 나갈 수 있다.
    SELECT att.attnotnull INTO chunk_id_notnull
    FROM pg_attribute att
    WHERE att.attrelid = relation AND att.attname = 'chunk_id' AND att.attnum > 0;

    IF chunk_id_notnull IS NULL THEN
        RAISE EXCEPTION 'training_rag_chunks column mismatch: chunk_id 열이 없습니다';
    END IF;
    IF NOT chunk_id_notnull THEN
        RAISE EXCEPTION 'training_rag_chunks column mismatch: chunk_id 에 NOT NULL 이 없습니다';
    END IF;

    -- ④ 옛 UNIQUE(document_id, chunk_index, embedding_model) 가 남아 있으면 안 된다.
    --    PK 와 같은 컬럼이라 인덱스가 둘이 된다 — 틀린 것은 아니지만 마이그레이션이
    --    끝까지 안 돈 흔적이므로 잡는다.
    IF EXISTS (
        SELECT 1 FROM pg_constraint con
        WHERE con.conrelid = relation
          AND con.contype = 'u'
          AND (SELECT array_agg(att.attname::text ORDER BY att.attname)
               FROM unnest(con.conkey) AS k(attnum)
               JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = k.attnum)
              = ARRAY['chunk_index', 'document_id', 'embedding_model']
    ) THEN
        RAISE EXCEPTION
            'training_rag_chunks unique constraint mismatch: 옛 UNIQUE(document_id, chunk_index, embedding_model) 이 남아 있습니다';
    END IF;

    -- ⑤ 문서 표로의 FK 는 그대로여야 한다. 키를 바꾸다 떨어뜨리면 고아 청크가 생긴다.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint con
        WHERE con.conrelid = relation
          AND con.contype = 'f'
          AND con.confrelid = to_regclass('training_rag_documents')
    ) THEN
        RAISE EXCEPTION
            'training_rag_chunks foreign key mismatch: training_rag_documents 로의 FK 가 없습니다';
    END IF;
END
$verify$;

-- ─────────────────────────────────────────────────────────────────────────────
-- 사람이 눈으로 보는 자리. 위 단언이 통과한 뒤에만 여기까지 온다.
-- ─────────────────────────────────────────────────────────────────────────────

-- 제약 목록. PK 는 셋, UNIQUE 는 (chunk_id, embedding_model) 하나여야 한다.
SELECT con.contype,
       con.conname,
       (SELECT string_agg(att.attname::text, ', ' ORDER BY k.ord)
        FROM unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
        JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = k.attnum) AS columns
FROM pg_constraint con
WHERE con.conrelid = to_regclass('training_rag_chunks') AND con.contype IN ('p', 'u', 'f')
ORDER BY con.contype, con.conname;

-- 모델이 몇 벌 들어 있나. **적용 직후에는 1 이 정상이다** — 이 마이그레이션은 공존을
-- 가능하게 할 뿐 벡터를 만들지 않는다. 다른 모델을 적재하면 그때 2 가 된다.
SELECT count(*) AS chunks,
       count(DISTINCT embedding_model) AS models,
       count(DISTINCT chunk_id) AS distinct_chunk_ids
FROM training_rag_chunks;
