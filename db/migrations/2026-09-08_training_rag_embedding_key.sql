-- training_rag_chunks 의 키를 바꾼다 — 한 청킹의 여러 임베딩이 공존하게.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- 왜
-- ─────────────────────────────────────────────────────────────────────────────
-- `chunk_id` 가 PK 이고 적재가 `ON CONFLICT(chunk_id)` 였다. 그래서 다른 임베딩 모델로
-- 재적재하면 `UNIQUE(document_id, chunk_index, embedding_model)` 이 공존을 허용하기 전에
-- **PK 에서 먼저 충돌해 옛 벡터를 덮어썼다.** 예외도 경고도 안 났고 로그는 성공으로 찍혔다.
--
-- 스키마의 의도는 처음부터 공존이었다 — 그 UNIQUE 가 그 뜻이고, 검색도 이미
-- `where embedding_model=%s` 로 한 모델만 본다. 깨져 있던 것은 적재 한 줄이었다.
--
-- ⚠ `chunk_id` 형식은 **안 바꾼다.** 그 값이 서빙 응답(`EvidenceCard`)과 평가 산출물
--   (`judgments_*.jsonl`, 리포트)의 id 로 나가 있어서, 바꾸면 기존 기록이 안 맞는다.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- 적용
-- ─────────────────────────────────────────────────────────────────────────────
--   docker compose exec -T pgvector psql -U <계정> -d vectordb \
--     -f - < db/migrations/2026-09-08_training_rag_embedding_key.sql
--
-- **여러 번 돌려도 안전하다.** 이 저장소에는 마이그레이션 버전 테이블이 없어서 무엇이
-- 적용됐는지 DB 가 기억하지 않는다 (CLAUDE.md). 그래서 전부 존재 검사를 두른다.
--
-- 데이터는 안 지운다. 행은 그대로 있고 제약만 바뀐다.

BEGIN;

-- ① 기존 PK(chunk_id) 를 떼어낸다.
--    이름을 하드코딩하지 않는다 — 손으로 만든 DB 는 다른 이름일 수 있다.
DO $$
DECLARE
  pk_name text;
BEGIN
  SELECT con.conname INTO pk_name
  FROM pg_constraint con
  WHERE con.conrelid = to_regclass('training_rag_chunks')
    AND con.contype = 'p'
    AND (SELECT array_agg(att.attname::text ORDER BY att.attname)
         FROM unnest(con.conkey) AS k(attnum)
         JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = k.attnum)
        = ARRAY['chunk_id'];

  IF pk_name IS NOT NULL THEN
    EXECUTE format('ALTER TABLE training_rag_chunks DROP CONSTRAINT %I', pk_name);
    RAISE NOTICE '옛 PK(chunk_id) 를 떼었습니다: %', pk_name;
  ELSE
    RAISE NOTICE '옛 PK(chunk_id) 가 없습니다 — 이미 적용된 DB 입니다.';
  END IF;
END $$;

-- ② UNIQUE(document_id, chunk_index, embedding_model) 를 뗀다.
--    같은 컬럼으로 PK 를 세울 것이라 그대로 두면 인덱스가 둘이 된다.
DO $$
DECLARE
  uq_name text;
BEGIN
  SELECT con.conname INTO uq_name
  FROM pg_constraint con
  WHERE con.conrelid = to_regclass('training_rag_chunks')
    AND con.contype = 'u'
    AND (SELECT array_agg(att.attname::text ORDER BY att.attname)
         FROM unnest(con.conkey) AS k(attnum)
         JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = k.attnum)
        = ARRAY['chunk_index', 'document_id', 'embedding_model'];

  IF uq_name IS NOT NULL THEN
    EXECUTE format('ALTER TABLE training_rag_chunks DROP CONSTRAINT %I', uq_name);
    RAISE NOTICE '옛 UNIQUE 를 떼었습니다: %', uq_name;
  END IF;
END $$;

-- ③ 새 PK. 적재의 ON CONFLICT 대상이 이것이다.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint con
    WHERE con.conrelid = to_regclass('training_rag_chunks') AND con.contype = 'p'
  ) THEN
    ALTER TABLE training_rag_chunks
      ADD CONSTRAINT training_rag_chunks_pkey
      PRIMARY KEY (document_id, chunk_index, embedding_model);
    RAISE NOTICE '새 PK(document_id, chunk_index, embedding_model) 를 세웠습니다.';
  END IF;
END $$;

-- ④ 소비자가 기대하는 불변식: 한 모델 안에서 chunk_id 는 유일하다.
--    검색이 늘 한 모델로 거르므로 결과 안에서는 id 하나가 청크 하나를 가리킨다.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint con
    WHERE con.conrelid = to_regclass('training_rag_chunks')
      AND con.contype = 'u'
      AND (SELECT array_agg(att.attname::text ORDER BY att.attname)
           FROM unnest(con.conkey) AS k(attnum)
           JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = k.attnum)
          = ARRAY['chunk_id', 'embedding_model']
  ) THEN
    ALTER TABLE training_rag_chunks
      ADD CONSTRAINT training_rag_chunks_chunk_id_model_key
      UNIQUE (chunk_id, embedding_model);
    RAISE NOTICE 'UNIQUE(chunk_id, embedding_model) 를 세웠습니다.';
  END IF;
END $$;

-- ⑤ chunk_id 는 이제 PK 가 아니므로 NOT NULL 을 명시적으로 건다.
--    (PK 였을 때는 자동이었다.)
ALTER TABLE training_rag_chunks ALTER COLUMN chunk_id SET NOT NULL;

COMMIT;

-- ─────────────────────────────────────────────────────────────────────────────
-- 확인 — 사람이 눈으로 본다
-- ─────────────────────────────────────────────────────────────────────────────

-- PK 는 셋, UNIQUE 는 (chunk_id, embedding_model) 이어야 한다.
SELECT con.contype,
       con.conname,
       (SELECT string_agg(att.attname, ', ' ORDER BY k.ord)
        FROM unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
        JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = k.attnum) AS columns
FROM pg_constraint con
WHERE con.conrelid = to_regclass('training_rag_chunks') AND con.contype IN ('p', 'u')
ORDER BY con.contype;

-- 행이 안 줄었는지. 마이그레이션 전 값과 같아야 한다.
SELECT count(*) AS chunks,
       count(DISTINCT embedding_model) AS models,
       count(DISTINCT chunk_id) AS distinct_chunk_ids
FROM training_rag_chunks;
