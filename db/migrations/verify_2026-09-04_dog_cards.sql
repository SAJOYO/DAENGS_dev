-- verify_2026-09-04_dog_cards.sql
-- 적용 후 눈으로 보는 질의 모음. **고치는 것은 없다.**

-- 1) 표가 생겼나.
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_name = 'dog_cards'
ORDER BY ordinal_position;

-- 2) ⚠️ **id 에 기본값이 없어야 한다.** 다른 표와 달리 `gen_random_uuid()` 를 안 건다 —
--    앱이 만든 UUID 를 그대로 받아야 재전송이 멱등해진다. 기본값이 붙어 있으면
--    앱이 안 보낸 경우 서버가 새 id 를 만들어 **같은 카드가 두 장** 생긴다.
SELECT column_name, column_default
FROM information_schema.columns
WHERE table_name = 'dog_cards' AND column_name = 'id';

-- 3) 검사들. core 사각형·얼굴 짝·크기.
SELECT conname, pg_get_constraintdef(oid) AS def
FROM pg_constraint
WHERE conrelid = 'dog_cards'::regclass AND contype = 'c'
ORDER BY conname;

-- 4) 외래키 둘. app_user 는 CASCADE, dog 는 **SET NULL** 이어야 한다 —
--    아이를 지워도 카드는 남습니다.
SELECT conname, pg_get_constraintdef(oid) AS def
FROM pg_constraint
WHERE conrelid = 'dog_cards'::regclass AND contype = 'f'
ORDER BY conname;

-- 5) 인덱스. face key 는 **부분 UNIQUE** 여야 한다 (얼굴 없는 카드가 여럿이므로).
SELECT indexname, indexdef
FROM pg_indexes
WHERE tablename = 'dog_cards'
ORDER BY indexname;

-- 6) 처음에는 비어 있어야 한다.
SELECT count(*) AS cards, count(face_storage_key) AS with_face FROM dog_cards;

-- 7) ⚠️ **탈퇴한 회원의 카드가 남아 있는지.** 0 이 나와야 한다.
--    app_users 행은 탈퇴해도 남으므로 CASCADE 가 안 돕니다 — 탈퇴 경로가 명시로
--    지우는데, 그게 실제로 도는지 보는 자리입니다.
SELECT count(*) AS orphan_after_withdrawal
FROM dog_cards c
JOIN app_users u ON u.id = c.app_user_id
WHERE u.status = 'withdrawn';

-- 8) 뒤집힌 사각형이 없는지. 0 이 나와야 한다 (검사가 막지만 확인용).
SELECT count(*) AS bad_rects
FROM dog_cards
WHERE core_right <= core_left OR core_bottom <= core_top;
