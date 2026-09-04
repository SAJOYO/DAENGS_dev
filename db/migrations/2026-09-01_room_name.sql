-- 2026-09-01_room_name.sql
-- 미니룸 이름표를 사용자가 정한다. db/init/03_auth.sql 과 같은 결과가 되게 한다.
--
-- **여러 번 돌려도 안전하다** (IF NOT EXISTS). 이 저장소는 버전 테이블이 없어서
-- DB 가 적용 여부를 기억하지 않는다 — db/migrations/README.md 참고.
--
-- 왜: 방 앞 이름표가 "네옹이네" 로 앱에 박혀 있었다. 누가 쓰든 남의 강아지 이름이
-- 자기 방에 걸려 있다.
--
-- NULL 은 "아직 안 정했다" 이고, 그때 앱이 대표 강아지 이름으로 짓는다.
-- 기존 회원은 전부 NULL 이라 예전과 같은 화면을 본다.

ALTER TABLE app_users
    ADD COLUMN IF NOT EXISTS room_name VARCHAR(20);

COMMENT ON COLUMN app_users.room_name IS '미니룸 이름표 / NULL 이면 앱이 대표 강아지 이름으로 짓는다';
