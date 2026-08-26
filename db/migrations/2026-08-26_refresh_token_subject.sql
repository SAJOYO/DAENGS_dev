-- =====================================================================
-- 2026-08-26_refresh_token_subject.sql
--
-- refresh_tokens 가 관리자 세션만 담던 것을 앱 회원 세션까지 담도록 넓힌다 (D-016).
-- 원본 스키마는 db/init/03_auth.sql 이고, 이 파일은 **이미 돌고 있는 DB** 용이다.
--
-- 기존 행은 admin_user_id 가 차 있으므로 새 CHECK 를 그대로 통과한다.
-- 서비스를 내리지 않고 돌릴 수 있다.
--
-- 여러 번 실행해도 안전하다.
-- =====================================================================

BEGIN;

-- 1) 앱 회원 소유자 컬럼.
--    app_users 는 03_auth.sql 에서 이미 만들어져 있어야 한다.
ALTER TABLE refresh_tokens
    ADD COLUMN IF NOT EXISTS app_user_id UUID
        REFERENCES app_users(id) ON DELETE CASCADE;

-- 2) 관리자 컬럼의 NOT NULL 을 푼다. 앱 회원 행에서는 이쪽이 비어야 한다.
ALTER TABLE refresh_tokens
    ALTER COLUMN admin_user_id DROP NOT NULL;

-- 3) 소유자가 정확히 하나. NOT NULL 을 푼 자리를 이 제약이 대신 지킨다.
--    **2번만 하고 이걸 빠뜨리면 주인 없는 세션 행이 만들어질 수 있다.**
ALTER TABLE refresh_tokens
    DROP CONSTRAINT IF EXISTS refresh_tokens_one_subject_check;
ALTER TABLE refresh_tokens
    ADD CONSTRAINT refresh_tokens_one_subject_check
        CHECK (num_nonnulls(admin_user_id, app_user_id) = 1);

-- 4) 인덱스. 각 행이 둘 중 한 컬럼만 채우므로 부분 인덱스로 만든다.
--    기존 idx_refresh_tokens_admin 은 전체 인덱스라 새로 만든다.
DROP INDEX IF EXISTS idx_refresh_tokens_admin;
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_admin
    ON refresh_tokens (admin_user_id) WHERE admin_user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_refresh_tokens_app
    ON refresh_tokens (app_user_id) WHERE app_user_id IS NOT NULL;

-- 5) 컬럼 설명.
COMMENT ON TABLE  refresh_tokens               IS '재발급 토큰 (관리자 + 앱 회원) / 강제 로그아웃용';
COMMENT ON COLUMN refresh_tokens.admin_user_id IS '소유 관리자 (계정 삭제 시 CASCADE) / app_user_id 와 배타';
COMMENT ON COLUMN refresh_tokens.app_user_id   IS '소유 앱 회원 (계정 삭제 시 CASCADE) / admin_user_id 와 배타';

COMMIT;

-- ---------------------------------------------------------------------
-- 적용 뒤 확인
--
--   \d refresh_tokens
--
-- admin_user_id 에 not null 이 없고, app_user_id 가 있고,
-- Check constraints 에 refresh_tokens_one_subject_check 가 보이면 된 것이다.
--
-- **이 마이그레이션 전에 발급된 access token 은 전부 무효가 된다** (typ 클레임이
-- 없어서). 수명이 5분이라 그 안에 정리되고, refresh 는 그대로 살아 있어서
-- 재발급 한 번이면 복구된다.
-- ---------------------------------------------------------------------
