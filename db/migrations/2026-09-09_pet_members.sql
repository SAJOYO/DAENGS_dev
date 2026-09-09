-- ---------------------------------------------------------------------
-- pet_members / pet_invites : 공동 돌봄 (docs/co-care.md)
-- ---------------------------------------------------------------------
-- 이미 도는 DB 에 손으로 적용한다. 버전 테이블이 없으므로 여러 번 돌려도 안전해야 한다.
-- 아래(두 표 · 인덱스 · 두 트리거)는 db/init/24_pet_members.sql 과 글자 그대로 같다.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pet_members (
    pet_id      UUID NOT NULL REFERENCES pets(id)      ON DELETE CASCADE,

    -- ⚠️ 이 CASCADE 는 **안 돈다.** 탈퇴가 app_users 행을 안 지우기 때문이다
    --    (services/app_auth.py 의 withdraw 는 status 만 바꾼다). 탈퇴한 돌보미를 지우는
    --    것은 아래 트리거다. 여기에 기대면 유령 돌보미가 남는다.
    app_user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,

    joined_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (pet_id, app_user_id)
);

-- PK 가 (pet_id, …) 라 "내가 돌보는 강아지 전부" 를 못 탄다. 그 조회가 제일 잦다
-- (앱을 켤 때마다). 지우지 말 것.
CREATE INDEX IF NOT EXISTS idx_pet_members_app_user ON pet_members (app_user_id);

CREATE TABLE IF NOT EXISTS pet_invites (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pet_id     UUID NOT NULL REFERENCES pets(id) ON DELETE CASCADE,

    -- 대표가 바뀌면 이전 대표가 뿌린 초대를 무효로 보는 데 쓴다. 이 CASCADE 도 위와
    -- 같은 이유로 안 돈다 — 트리거가 지운다.
    invited_by UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,

    -- 평문은 저장하지 않는다 (refresh_tokens 와 같은 규칙). core/token.py 의
    -- hash_refresh_token 이 만드는 sha256 hex 64자다.
    token_hash CHAR(64) NOT NULL UNIQUE,

    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pet_invites_pet ON pet_invites (pet_id);

-- accepted_at 칸이 없다. 수락하면 행을 지운다 — 그것만으로 일회용이 되고, "이미 쓴 초대"와
-- "없는 토큰"이 같은 404 가 되어 정보도 덜 샌다.

-- ① app_users 는 탈퇴해도 살아남으므로 FK 로는 절대 안 지워진다.
--   21_activity_game.sql 의 activity_owner_cleanup 과 같은 선례다.
CREATE OR REPLACE FUNCTION pet_membership_owner_cleanup() RETURNS trigger LANGUAGE plpgsql AS $func$
BEGIN
    IF NEW.status = 'withdrawn' THEN
        DELETE FROM pet_members WHERE app_user_id = NEW.id;
        DELETE FROM pet_invites  WHERE invited_by  = NEW.id;
    END IF;
    RETURN NEW;
END $func$;
DROP TRIGGER IF EXISTS pet_membership_owner_cleanup ON app_users;
CREATE TRIGGER pet_membership_owner_cleanup AFTER UPDATE OF status ON app_users
FOR EACH ROW EXECUTE FUNCTION pet_membership_owner_cleanup();

-- ② 대표가 돌보미로도 들어오는 것을 DB 가 거절한다. 서비스 검증과 겹치지만, 겹치는 것이
--   정합성 제약의 목적이다.
CREATE OR REPLACE FUNCTION pet_members_not_owner() RETURNS trigger LANGUAGE plpgsql AS $func$
BEGIN
    IF NEW.app_user_id = (SELECT app_user_id FROM pets WHERE id = NEW.pet_id) THEN
        RAISE EXCEPTION '대표는 돌보미가 될 수 없다 (pet=%, user=%)', NEW.pet_id, NEW.app_user_id;
    END IF;
    RETURN NEW;
END $func$;
DROP TRIGGER IF EXISTS pet_members_not_owner ON pet_members;
CREATE TRIGGER pet_members_not_owner BEFORE INSERT OR UPDATE ON pet_members
FOR EACH ROW EXECUTE FUNCTION pet_members_not_owner();

COMMENT ON TABLE pet_members IS
    '공동 돌봄의 돌보미. 대표는 pets.app_user_id 에 있다 (docs/co-care.md)';

-- care_events.app_user_id 는 이제 '소유자' 가 아니라 '챙긴 사람' 이다. 개명이 안 됐으면
-- 여기서 하고, nullable 로 내리고, FK 를 SET NULL 로 바꾼다. 버전 테이블이 없으므로
-- 여러 번 돌려도 안전하도록 information_schema 로 감싼다.
DO $mig$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'care_events' AND column_name = 'app_user_id'
    ) THEN
        ALTER TABLE care_events RENAME COLUMN app_user_id TO actor_app_user_id;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'care_events' AND column_name = 'actor_app_user_id'
          AND is_nullable = 'NO'
    ) THEN
        ALTER TABLE care_events ALTER COLUMN actor_app_user_id DROP NOT NULL;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'care_events' AND constraint_name = 'care_events_app_user_id_fkey'
    ) THEN
        ALTER TABLE care_events DROP CONSTRAINT care_events_app_user_id_fkey;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'care_events' AND constraint_name = 'care_events_actor_fkey'
    ) THEN
        ALTER TABLE care_events ADD CONSTRAINT care_events_actor_fkey
            FOREIGN KEY (actor_app_user_id) REFERENCES app_users(id) ON DELETE SET NULL;
    END IF;
END $mig$;
