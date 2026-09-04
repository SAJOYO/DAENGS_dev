-- verify_2026-09-04_pet_photo.sql
-- 적용 후 눈으로 보는 질의 모음. **고치는 것은 없다.**

-- 1) 칸 여덟 개가 생겼나. 전부 NULL 을 허용해야 한다 — 사진은 선택이다.
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'pets' AND column_name LIKE 'photo\_%'
ORDER BY column_name;

-- 2) 검사 다섯 개가 붙었나.
SELECT conname, pg_get_constraintdef(oid) AS def
FROM pg_constraint
WHERE conrelid = 'pets'::regclass AND conname LIKE 'pets_photo%'
ORDER BY conname;

-- 3) 인덱스 둘. 둘 다 **부분 인덱스**(WHERE ... IS NOT NULL)여야 한다 —
--    안 그러면 사진 없는 행들이 NULL 하나로 UNIQUE 충돌한다.
SELECT indexname, indexdef
FROM pg_indexes
WHERE tablename = 'pets' AND indexname LIKE 'idx_pets_photo%'
ORDER BY indexname;

-- 4) 기존 행은 전부 사진이 없어야 한다 — 아직 아무도 안 올렸다.
SELECT count(*) AS pets_total,
       count(photo_storage_key) AS with_photo,
       count(photo_pending_key) AS pending
FROM pets;

-- 5) 반쪽 행이 없는지. **0 이 나와야 한다.**
--    (검사가 막아 주지만, 검사가 붙기 전에 들어간 행이 있는지 확인하는 자리다)
SELECT count(*) AS broken_rows
FROM pets
WHERE (photo_storage_key IS NULL) <> (photo_generation IS NULL)
   OR (photo_pending_key IS NULL) <> (photo_pending_at IS NULL);

-- 6) 짝 검사가 실제로 막는지. **이 줄은 실패해야 맞다** (에러가 나면 통과다).
--    확인만 하고 되돌리므로 데이터는 안 바뀐다.
-- BEGIN;
-- UPDATE pets SET photo_storage_key = 'pets/x/profile/y.jpg'
--  WHERE id = (SELECT id FROM pets LIMIT 1);
-- ROLLBACK;
