-- 2026-09-05_app_user_nickname.sql
-- 회원에게 사람 이름을 하나 준다. db/init/03_auth.sql 과 같은 결과가 되게 한다.
--
-- **여러 번 돌려도 안전하다** (IF NOT EXISTS). 이 저장소는 버전 테이블이 없어서
-- DB 가 적용 여부를 기억하지 않는다 — db/migrations/README.md 참고.
--
-- 왜: 카카오 앱키가 사업자 등록이 아니라 프로젝트 팀 것이라 이메일·전화번호·이름
-- 동의를 못 받는다. 그래서 email_enc · phone_enc · name_enc 가 전부 NULL 이고,
-- 관리자 콘솔에서 회원 한 줄이 "UUID · 숫자 · None · None · None" 이다. 회원 한 명을
-- 사람 말로 가리킬 방법이 없다.
--
-- room_name 으로 겸하지 않는 이유: 저건 집 이름이다("네옹이네"). 앱의 RoomLabel.kt 가
-- 가구 이름으로 만들기 때문에 사람 이름과 섞으면 화면마다 뜻이 갈린다.
--
-- **UNIQUE 를 lower() 표현식으로 건다.** 그냥 컬럼 UNIQUE 면 'Neo' 와 'neo' 가 둘 다
-- 생기는데, 화면에서 그 둘은 같은 이름으로 읽혀서 "고유하게 구분한다"가 그 자리에서
-- 깨진다. NULL 은 여럿 허용된다 — 아직 발급 안 된 회원이 여럿일 수 있어야 한다.
--
-- **기존 회원은 전부 NULL 로 남는다.** 여기서 채우지 않는 이유는, SQL 로 지으면
-- 이름 짓는 규칙이 두 벌(SQL 과 services/app_auth.py)이 되기 때문이다. 대신 서버가
-- "닉네임이 비어 있으면 발급한다"로 되어 있어서 **다음 로그인에 저절로 채워진다.**

ALTER TABLE app_users
    ADD COLUMN IF NOT EXISTS nickname VARCHAR(30);

CREATE UNIQUE INDEX IF NOT EXISTS idx_app_users_nickname
    ON app_users (lower(nickname));

COMMENT ON COLUMN app_users.nickname IS '사람 이름 / 가입 때 서버가 발급. lower() 로 유일. NULL 이면 아직 발급 전';
