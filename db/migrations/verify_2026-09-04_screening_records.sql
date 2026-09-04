-- verify_2026-09-04_screening_records.sql
-- 적용 후 눈으로 보는 질의 모음. **고치는 것은 없다.**

-- 1) 표가 생겼나. 열 구성을 눈으로 확인한다.
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_name = 'screening_records'
ORDER BY ordinal_position;

-- 2) 검사 셋. status · content_type · size 가 붙었어야 한다.
SELECT conname, pg_get_constraintdef(oid) AS def
FROM pg_constraint
WHERE conrelid = 'screening_records'::regclass AND contype = 'c'
ORDER BY conname;

-- 3) 외래키 둘. app_user 는 CASCADE, pet 은 **SET NULL** 이어야 한다 —
--    아이를 지워도 기록은 남습니다.
SELECT conname, pg_get_constraintdef(oid) AS def
FROM pg_constraint
WHERE conrelid = 'screening_records'::regclass AND contype = 'f'
ORDER BY conname;

-- 4) 인덱스 셋. photo_storage_key 는 **UNIQUE** 여야 한다.
SELECT indexname, indexdef
FROM pg_indexes
WHERE tablename = 'screening_records'
ORDER BY indexname;

-- 5) updated_at 트리거가 붙었나.
SELECT tgname
FROM pg_trigger
WHERE tgrelid = 'screening_records'::regclass AND NOT tgisinternal;

-- 6) 처음에는 비어 있어야 한다.
SELECT count(*) AS records FROM screening_records;

-- 7) ⚠️ **탈퇴한 회원의 기록이 남아 있는지.** 0 이 나와야 한다.
--    app_users 행은 탈퇴해도 남으므로 CASCADE 가 안 돕니다 — 탈퇴 경로가 명시로
--    지우는데, 그게 실제로 도는지 보는 자리입니다.
SELECT count(*) AS orphan_after_withdrawal
FROM screening_records s
JOIN app_users u ON u.id = s.app_user_id
WHERE u.status = 'withdrawn';
