-- 2026-09-06_documents_category_insurance.sql — insurance 를 policy 에서 분리한다 (RAG-067 / #271)
--
-- **왜 필요한가.** 보험 약관(`insurer-terms-pdfs` 4,563) + 공시(`knia-disclosure` 110) = 4,673행이
-- `policy` 에 들어 있어 코퍼스의 **47.5%가 한 값**을 갖는다. `db/init/01_schema.sql` 의 주석이
-- *"필요해지면 제약만 교체하면 된다"* 고 예고해 둔 자리이고, 사람이 2026-08-30 에 "나중에 뺀다"로
-- 방향을 정했다.
--
-- **재임베딩·재청킹은 없다.** `content` 를 안 건드리므로 벡터가 그대로다 — 스키마 주석의
-- *"재분류는 UPDATE 한 줄이고 content가 안 바뀌므로 임베딩 재계산이 필요 없다"* 그대로다.
--
-- ⚠ **이 파일이 값까지 채우는 것은 GCP 때문이다.** 집 서버는 코퍼스(.meta.json → parsed → 청크)가
--    `category` 의 원천이라 `rag load` 가 같은 결과를 만든다 — 여기 UPDATE 는 0행이 된다.
--    **GCP VM 에는 코퍼스가 없어**(docs/deploy/roadmap.md §2-4) 이 파일이 유일한 경로다.
--
--    `2026-09-05_documents_org_backfill.sql` 과 다른 점 하나 — **doc_id 목록을 굽지 않아도 된다.**
--    `subcategory` 가 이미 'insurance' 라 그 한 줄로 정확히 4,673행이 잡힌다.
--
-- ⚠ **`org` 이 지워진 사고를 여기서 반복하지 않는다** (RAG-066 ①). 저쪽은 마이그레이션으로만
--    DB 에 있던 값이라 다음 `rag load` 가 덮어써 지웠다. 이 카드는 같은 변경을 **코퍼스에도**
--    내렸다(소스 클래스 + .meta.json 정정 + 재파싱). 그래서 적재가 이 값을 다시 만든다.
--
-- **여러 번 돌려도 안전하다** (CLAUDE.md — 버전 테이블이 없다).
--   · 제약은 DROP IF EXISTS 뒤 ADD 라 이름이 같아도 안 부딪힌다
--   · UPDATE 는 `IS DISTINCT FROM` 으로 이미 맞은 행을 건너뛴다
--
-- 적용 뒤: db/migrations/verify_2026-09-06_documents_category_insurance.sql
-- **두 DB 에 각각 적용한다** — 집 서버는 `rag load` 보다 **먼저**여야 한다(안 그러면 CHECK 위반).

BEGIN;

-- ① 제약 교체. 값이 늘어나는 방향이라 기존 행은 하나도 안 걸린다.
--    이름은 Postgres 가 자동으로 붙이는 `{table}_{column}_check` 규약을 그대로 쓴다.
ALTER TABLE documents DROP CONSTRAINT IF EXISTS documents_category_check;
ALTER TABLE documents ADD  CONSTRAINT documents_category_check
    CHECK (category IN ('policy', 'travel', 'food', 'insurance'));

-- ② 값 채우기 (GCP 용). 집 서버에서는 코퍼스가 이미 같은 값을 만들어 0행이다.
UPDATE documents
   SET category = 'insurance'
 WHERE subcategory = 'insurance'
   AND category IS DISTINCT FROM 'insurance';

COMMIT;
