-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — `tools/check_migration_verification.py` 가 스키마를 일부러 망가뜨린 뒤
-- 이 파일이 그것을 잡는지 본다. 그래서 눈으로 보는 SELECT 가 아니라 RAISE EXCEPTION 이어야 하고,
-- 메시지에 `mismatch` 또는 `missing table` 이 들어가야 한다 (하네스가 그 낱말로 판정한다).
-- 사람이 읽을 분포표는 맨 아래에 있다.
DO $verify$
DECLARE
    definition text;
    offenders bigint;
    want text;
BEGIN
    IF to_regclass('documents') IS NULL THEN
        RAISE EXCEPTION 'missing table: documents';
    END IF;

    -- ① 제약이 살아 있고 **검증된 상태**인가. NOT VALID 로 바꿔치기하면 여기서 걸린다.
    SELECT pg_get_constraintdef(c.oid) INTO definition
    FROM pg_constraint c
    WHERE c.conrelid = to_regclass('documents')
      AND c.conname = 'documents_category_check'
      AND c.contype = 'c'
      AND c.convalidated;
    IF definition IS NULL THEN
        RAISE EXCEPTION 'constraint mismatch: documents_category_check missing or not validated';
    END IF;

    -- ② 네 값을 **전부** 받는가. 하나라도 빠지면 기존 행이 CHECK 위반이 되거나
    --    insurance 를 못 넣는다. 늘어나는 방향의 변경이라 옛 셋도 남아 있어야 한다.
    FOREACH want IN ARRAY ARRAY['policy', 'travel', 'food', 'insurance'] LOOP
        IF position('''' || want || '''' IN definition) = 0 THEN
            RAISE EXCEPTION 'constraint mismatch: documents_category_check lacks %, got %',
                want, definition;
        END IF;
    END LOOP;

    -- ③ 옮겨야 할 행이 남아 있는가. 마이그레이션이 덜 돌았거나 뒤에 `rag load` 가
    --    옛 값으로 덮어썼으면(RAG-066 ①) 여기서 걸린다.
    SELECT count(*) INTO offenders
    FROM documents WHERE subcategory = 'insurance' AND category <> 'insurance';
    IF offenders > 0 THEN
        RAISE EXCEPTION 'row mismatch: % rows still on the old category', offenders;
    END IF;

    -- ④ 엉뚱한 행을 옮기지 않았는가. 반대 방향도 본다 — 조건을 잘못 쓰면 이쪽으로 샌다.
    SELECT count(*) INTO offenders
    FROM documents WHERE category = 'insurance' AND subcategory <> 'insurance';
    IF offenders > 0 THEN
        RAISE EXCEPTION 'row mismatch: % rows moved without the insurance subcategory', offenders;
    END IF;
END
$verify$;

-- ─────────────────────────────────────────────────────────────────────────
-- 사람이 눈으로 보는 자리. 위 단언이 통과한 뒤에만 여기까지 온다.
-- ─────────────────────────────────────────────────────────────────────────

-- 분포. 2026-09-06 적용 직후 실측:
--   insurance 4,673 · policy 4,636 · food 272 · travel 255   (합 9,836)
-- **policy 와 insurance 가 비슷해야 한다** — 그것이 이 카드가 한 일이다.
SELECT category, count(*) AS rows,
       round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct
FROM documents GROUP BY category ORDER BY rows DESC;

-- 옮겨진 것이 그 두 소스뿐인가. insurer-terms-pdfs 4,563 · knia-disclosure 110 이 나와야 한다.
-- (일회용 스키마의 픽스처에는 metadata 가 비어 있어 0행이다 — 실물 DB 에서만 뜻이 있다)
SELECT metadata->>'source_id' AS source_id, count(*) AS rows
FROM documents WHERE category = 'insurance' AND metadata ? 'source_id'
GROUP BY 1 ORDER BY 2 DESC;
