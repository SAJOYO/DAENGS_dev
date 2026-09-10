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
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- 수락 영수증 (2026-09-10, docs/co-care.md §3, #388 · #261). 수락은 더 이상 이 행을
    -- 안 지운다 — 대신 이 둘을 채운다. 같은 사람이 같은 토큰으로 다시 오면 이 값으로
    -- 그때의 응답을 그대로 돌려준다(200). 다른 사람이 오면 여전히 404 다. 수명은 새 칸을
    -- 안 두고 위 expires_at 그대로 쓴다 — 만료건 청소가 수락 여부를 안 가린다.
    accepted_at TIMESTAMPTZ,
    -- SET NULL 은 care_events.actor_app_user_id 와 같은 이유다 — 영수증은 행을 지우지
    -- 않고 사람만 비운다. app_users 는 탈퇴해도 행이 안 지워지므로(위 "함정") 이 SET NULL
    -- 은 실질적으로 거의 안 돈다.
    accepted_by UUID REFERENCES app_users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_pet_invites_pet ON pet_invites (pet_id);

-- ① app_users 는 탈퇴해도 살아남으므로 FK 로는 절대 안 지워진다.
--   21_activity_game.sql 의 activity_owner_cleanup 과 같은 선례다.
--
--   **care_events 의 UPDATE 도 그래서 여기 있다.** 돌보미가 남의 집 강아지에 약을 적고
--   탈퇴하면 actor_app_user_id 에 그 사람의 id 가 그대로 남는다 — 그 열의 FK 가 SET NULL 이지만
--   행이 지워져야 도는데 탈퇴는 행을 안 지운다(위 CASCADE 와 같은 함정).
--   공동 돌봄 이전에는 케어 기록이 대표의 것이라 대표가 탈퇴하면 강아지와 함께 사라졌고,
--   지금은 **남의 집 기록에 내 흔적이 남는** 새 보존이다. 남는 것은 닉네임이 아니라
--   **가명 id 하나**다 — 이름은 services/pet_member.py 의 actor_label 이 비구성원에게 이미 안
--   내준다. 그래도 탈퇴한 사람과 그 집을 잇는 식별자라 같이 지운다.
--   **행은 안 지우고 사람만 비운다** — "그날 밥을 먹은 사실" 은 강아지의 것이다.
CREATE OR REPLACE FUNCTION pet_membership_owner_cleanup() RETURNS trigger LANGUAGE plpgsql AS $func$
BEGIN
    IF NEW.status = 'withdrawn' THEN
        DELETE FROM pet_members WHERE app_user_id = NEW.id;
        DELETE FROM pet_invites  WHERE invited_by  = NEW.id;
        UPDATE care_events SET actor_app_user_id = NULL WHERE actor_app_user_id = NEW.id;
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
