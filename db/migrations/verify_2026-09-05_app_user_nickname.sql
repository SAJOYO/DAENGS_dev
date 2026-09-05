-- verify_2026-09-05_app_user_nickname.sql
-- 적용 후 눈으로 보는 질의 모음. **고치는 것은 없다.**

-- 1) 칸이 생겼나. VARCHAR(30) 이고 NULL 을 허용해야 한다 — NULL 이 "아직 발급 전"이다.
SELECT column_name, data_type, character_maximum_length, is_nullable
FROM information_schema.columns
WHERE table_name = 'app_users' AND column_name = 'nickname';

-- 2) 인덱스가 **표현식(lower)** 인가. 그냥 컬럼 UNIQUE 면 'Neo' 와 'neo' 가 둘 다 생긴다.
--    indexdef 에 lower(nickname) 이 보여야 통과다.
SELECT indexname, indexdef
FROM pg_indexes
WHERE tablename = 'app_users' AND indexname = 'idx_app_users_nickname';

-- 3) 기존 회원은 전부 NULL 이어야 한다 — 여기서 채우지 않는다.
--    다음 로그인에 서버가 발급한다 (services/app_auth.py 의 _ensure_nickname).
SELECT count(*) AS users_total,
       count(nickname) AS with_nickname,
       count(*) - count(nickname) AS pending
FROM app_users;

-- 4) 중복이 없나. **0 이 나와야 한다.** (아직 아무도 없으므로 당연히 0 이지만,
--    다시 돌렸을 때 인덱스가 살아 있는지 함께 확인하는 자리다)
SELECT lower(nickname) AS folded, count(*) AS n
FROM app_users
WHERE nickname IS NOT NULL
GROUP BY lower(nickname)
HAVING count(*) > 1;

-- 5) NULL 이 여럿 허용되는지. **에러가 안 나야 통과다.** 되돌리므로 데이터는 안 바뀐다.
--    (UNIQUE 인덱스가 NULL 을 중복으로 보면 아직 발급 안 된 회원이 둘 이상일 수 없다)
-- BEGIN;
-- INSERT INTO app_users (kakao_id) VALUES (-9001), (-9002);
-- ROLLBACK;

-- 6) 대소문자만 다른 이름이 막히는지. **이 줄은 실패해야 맞다** (에러가 나면 통과다).
-- BEGIN;
-- INSERT INTO app_users (kakao_id, nickname) VALUES (-9003, 'Neo'), (-9004, 'neo');
-- ROLLBACK;
