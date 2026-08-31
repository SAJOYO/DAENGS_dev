-- verify_2026-09-01_walk_pets.sql
-- 마이그레이션이 제대로 돌았는지 눈으로 본다. 아무것도 바꾸지 않는다.
--
--   docker compose exec -T pgvector psql -U <앱계정> -d vectordb -f - \
--       < db/migrations/verify_2026-09-01_walk_pets.sql

-- 1) walks.pet_id 가 사라졌는가. 0 줄이어야 한다.
SELECT column_name
FROM information_schema.columns
WHERE table_name = 'walks' AND column_name = 'pet_id';

-- 2) walk_pets 가 생겼는가. walk_id, pet_id 두 줄이어야 한다.
SELECT column_name, is_nullable
FROM information_schema.columns
WHERE table_name = 'walk_pets'
ORDER BY ordinal_position;

-- 3) 강아지가 붙어 있던 산책이 그대로 옮겨졌는가.
--    옮기기 전 `SELECT count(*) FROM walks WHERE pet_id IS NOT NULL` 과 같아야 한다.
SELECT count(*) AS 연결된_산책 FROM walk_pets;

-- 4) 강아지가 안 붙은 산책도 남아 있는가. **산책은 사람의 것이라 지우지 않는다.**
SELECT count(*) AS 전체_산책 FROM walks;
