-- ---------------------------------------------------------------------
-- pet_members / pet_invites : 공동 돌봄 (docs/co-care.md)
-- ---------------------------------------------------------------------
-- 이미 도는 DB 에 손으로 적용한다. 버전 테이블이 없으므로 여러 번 돌려도 안전해야 한다.
-- 아래(두 표 · 인덱스 · 두 트리거)는 db/init/24_pet_members.sql 과 글자 그대로 같다.
--
-- ⚠️ **머지보다 먼저 적용해야 한다.** 이 파일은 덧붙이기만 하지 않는다 —
--    care_events.app_user_id 를 actor_app_user_id 로 **개명**한다. README 의 "옛 코드가 도는
--    상태에서 먼저 적용해도 안전" 은 덧붙이기 전용 마이그레이션 이야기라 여기엔 그대로
--    적용되지 않는다. 그래도 순서는 여전히 "적용 먼저, 머지 나중" 이다:
--
--      적용 먼저 → 지금 도는 backend 가 app_user_id 를 SELECT 해서 케어 로그만 500.
--      머지 먼저 → 새 코드가 pet_members · actor_app_user_id 를 찾는다. 그 둘이 없으면
--                 get_accessible 을 쓰는 **모든 경로**(케어 · 대화 · 보행 · 산책 기록)가 죽는다.
--
--    두 번째가 훨씬 넓다. 그리고 dev 머지는 곧 자동 배포다(deploy.yml).
--    적용은 Actions 탭의 `db-migrate.yml` 로 한다 — ref 에 아직 머지 안 된 PR 브랜치를
--    넣을 수 있고, verify=true 면 짝 파일이 적용 직후 스키마를 단언으로 검사한다.
--    서버 PC 터미널에서 직접 하지 말 것 (db/migrations/README.md "함정 셋").
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
