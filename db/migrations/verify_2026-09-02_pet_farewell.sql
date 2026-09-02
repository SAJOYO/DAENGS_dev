-- verify_2026-09-02_pet_farewell.sql
-- 적용 후 눈으로 보는 질의 모음. **고치는 것은 없다.**

-- 1) 칸이 생겼나. 타입이 date 여야 한다 (timestamp 가 아니다).
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'pets' AND column_name = 'farewell_on';

-- 2) 검사 두 개가 붙었나.
SELECT conname, pg_get_constraintdef(oid) AS def
FROM pg_constraint
WHERE conrelid = 'pets'::regclass AND conname LIKE 'pets_farewell%'
ORDER BY conname;

-- 3) 기존 행은 전부 NULL 이어야 한다 — 아직 아무도 배웅하지 않았다.
SELECT count(*) AS pets_total,
       count(farewell_on) AS farewelled
FROM pets;

-- 4) 앞날이 막히는지. **이 줄은 실패해야 맞다** (에러가 나면 통과다).
--    확인만 하고 되돌리므로 데이터는 안 바뀐다.
-- BEGIN;
-- UPDATE pets SET farewell_on = CURRENT_DATE + 1 WHERE id = (SELECT id FROM pets LIMIT 1);
-- ROLLBACK;
