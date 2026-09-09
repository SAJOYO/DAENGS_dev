-- ---------------------------------------------------------------------
-- pet_members / pet_invites : 공동 돌봄 (docs/co-care.md)
-- ---------------------------------------------------------------------
-- Order: … -> 03_auth -> 05_pets -> 23_care_events -> 24_pet_members
--
-- 대표는 여기 없다. `pets.app_user_id` 가 대표이고 이 표는 돌보미만 담는다.
-- 구성원 = 대표 ∪ 돌보미. role 칸을 두지 않은 이유가 이것이다 — 한 사람이 양쪽에
-- 동시에 있을 수 없으니 어긋날 자리가 없다.
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
