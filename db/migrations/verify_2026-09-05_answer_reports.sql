-- Read-only verification for 2026-09-05_answer_reports.sql.
-- 적용 여부를 DB 가 기억하지 않으므로(버전 테이블 없음) 이것으로 확인한다.

-- 1) 표가 있어야 한다.
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public' AND table_name = 'answer_reports';

-- 2) 컬럼과 NULL 허용.
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'answer_reports'
ORDER BY ordinal_position;

-- 3) **turn_id 는 CASCADE 여야 한다** — D-053 이 A1 에 넘긴 배선이다.
--    탈퇴로 대화가 지워질 때 신고가 함께 사라지는 것이 이 한 줄에 달려 있다.
--    reviewed_by 는 RESTRICT 여야 한다 (관리자는 지우지 않고 정지시킨다).
SELECT tc.table_name, kcu.column_name, rc.delete_rule
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON tc.constraint_name = kcu.constraint_name
JOIN information_schema.referential_constraints rc
  ON tc.constraint_name = rc.constraint_name
WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_name = 'answer_reports'
ORDER BY kcu.column_name;

-- 4) CHECK 넷과 UNIQUE 하나.
SELECT conname, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conrelid = 'answer_reports'::regclass AND contype IN ('c', 'u')
ORDER BY conname;

-- 5) 인덱스 셋.
SELECT indexname, indexdef
FROM pg_indexes
WHERE tablename = 'answer_reports'
ORDER BY indexname;
