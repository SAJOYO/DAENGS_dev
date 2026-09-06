-- verify_2026-09-06_documents_category_insurance.sql
-- 적용 후 눈으로 보는 질의 모음. **고치는 것은 없다.**

-- 1) 제약이 네 값을 받는가. `insurance` 가 보여야 통과다.
SELECT conname, pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE conrelid = 'documents'::regclass AND conname = 'documents_category_check';

-- 2) 분포. 2026-09-06 적용 직후 기대값:
--      policy 4,636 · insurance 4,673 · food 272 · travel 255   (합 9,836)
--    **policy 와 insurance 가 비슷해야 한다** — 그것이 이 카드가 한 일이다.
SELECT category, count(*) AS rows,
       round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct
FROM documents GROUP BY category ORDER BY rows DESC;

-- 3) **어긋난 행이 없는가.** 둘 다 0 이어야 한다.
--    · subcategory 는 insurance 인데 category 가 아직 policy → 마이그레이션이 덜 돌았다
--    · category 는 insurance 인데 subcategory 가 아니다 → 엉뚱한 행을 옮겼다
SELECT
  count(*) FILTER (WHERE subcategory = 'insurance' AND category <> 'insurance') AS not_moved,
  count(*) FILTER (WHERE category = 'insurance' AND subcategory <> 'insurance') AS moved_wrongly
FROM documents;

-- 4) 옮겨진 것이 **그 두 소스뿐**인가. insurer-terms-pdfs 와 knia-disclosure 만 나와야 한다.
SELECT metadata->>'source_id' AS source_id, count(*) AS rows
FROM documents WHERE category = 'insurance'
GROUP BY 1 ORDER BY 2 DESC;

-- 5) **벡터가 그대로인가.** 이 카드는 content 를 안 건드린다 —
--    embedding 이 NULL 인 행이 생겼다면 적재가 잘못 돈 것이다. 0 이어야 한다.
SELECT count(*) AS insurance_without_vector
FROM documents WHERE category = 'insurance' AND embedding IS NULL;

-- 6) 없는 값이 막히는지. **이 줄은 실패해야 맞다** (에러가 나면 통과다).
-- BEGIN;
-- UPDATE documents SET category = 'care' WHERE id = (SELECT id FROM documents LIMIT 1);
-- ROLLBACK;
