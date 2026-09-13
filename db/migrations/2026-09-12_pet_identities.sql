-- ---------------------------------------------------------------------
-- pet_identities : 논리 강아지 (공동 돌봄 다중 초대 MVP)
-- ---------------------------------------------------------------------
-- 적용: docker compose exec -T pgvector psql -U <앱계정> -d vectordb -f - < 이 파일
--       또는 Actions 의 `DB 마이그레이션 적용`(db-migrate.yml), verify=true
--
-- ⚠ 서버 PC 터미널에서 직접 하지 말 것 — db/migrations/README.md 의 "함정 셋".
--
-- **덧붙이기만 한다.** 기존 `pets` 행도, 13개 표의 `pet_id` 기록도 옮기거나 지우지
-- 않는다. 여러 pet 행이 같은 실제 강아지라는 **관계만** 더한다 (물리 병합 금지).
-- 그래서 rollback 이 데이터 손실 0 이다 — 아래 "되돌리기" 참고.
--
-- backfill 이 없다. 아직 연결이 하나도 없으므로 `pet_identities` 는 비어서 시작하고
-- `pets.identity_id` 는 전부 NULL 이다 (`pet_members` 가 그랬던 것과 같다).
-- NULL 은 "연결 안 된 보통 강아지" 라는 뜻이고, 상한 계산이
-- `COUNT(DISTINCT COALESCE(identity_id, id))` 라 기존 계정의 숫자가 안 바뀐다.
--
-- 버전 테이블이 없으므로 **여러 번 돌려도 안전해야 한다** (CLAUDE.md).
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS pet_identities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- 그룹의 주보호자를 **사람이 아니라 대표 pet 행**으로 가리킨다.
    -- `pet_members` 에 role 칸을 안 둔 것과 같은 이유다: 주보호자의 원본은
    -- `pets.app_user_id` 하나뿐이라, 여기에 사람 id 를 또 두면 승계 때 두 곳이 어긋난다.
    --
    -- CASCADE 인 이유 — 그룹의 앵커 pet 행이 지워지면 그룹 자체가 뜻을 잃는다.
    -- 그때 이 행이 사라지고, 아래 `pets.identity_id` 의 SET NULL 이 남은 행들을
    -- 다시 독립 강아지로 되돌린다. **남은 사람들의 pet 행과 기록은 그대로 남는다.**
    owner_pet_id UUID NOT NULL REFERENCES pets(id) ON DELETE CASCADE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 한 pet 행이 두 그룹의 앵커일 수 없다.
CREATE UNIQUE INDEX IF NOT EXISTS pet_identities_owner_pet
    ON pet_identities (owner_pet_id);

-- NULL 이면 연결 안 된 보통 강아지다.
--
-- SET NULL 인 이유 — 그룹이 사라져도 **pet 행은 남아야 한다.** CASCADE 로 바꾸면
-- 그룹 행 하나 때문에 사람들의 강아지와 기록이 통째로 사라진다
-- (`pet_invites.accepted_by` 와 같은 결의 함정).
ALTER TABLE pets ADD COLUMN IF NOT EXISTS identity_id UUID
    REFERENCES pet_identities(id) ON DELETE SET NULL;

-- "이 그룹의 pet 행 전부" 가 케어·산책 공동 조회의 첫 걸음이다. 그 조회가 제일 잦다.
CREATE INDEX IF NOT EXISTS idx_pets_identity ON pets (identity_id);

-- **한 사람은 같은 그룹에 pet 행을 둘 이상 가질 수 없다.**
--
-- 이것이 "기존 강아지 하나를 초대 강아지 두 마리에 연결" 을 DB 가 거절하는 자리이고,
-- 목록 접기(`identity 안에서 내가 대표인 행` 하나 고르기)가 **항상 한 장**을 내놓는
-- 근거다. 서비스 검증과 겹치지만, 겹치는 것이 정합성 제약의 목적이다.
--
-- 부분 인덱스인 이유 — 연결 안 된 행은 `identity_id` 가 전부 NULL 이라, 조건이 없으면
-- (NULL 은 UNIQUE 에서 서로 안 부딪히므로) 인덱스만 쓸데없이 커진다.
CREATE UNIQUE INDEX IF NOT EXISTS pets_identity_one_per_user
    ON pets (identity_id, app_user_id) WHERE identity_id IS NOT NULL;

COMMENT ON TABLE pet_identities IS
    '논리 강아지. 여러 pets 행이 같은 실제 강아지임을 나타낸다 (docs/co-care.md)';
COMMENT ON COLUMN pets.identity_id IS
    'NULL 이면 연결 안 된 보통 강아지. 같은 값이면 같은 실제 강아지다';

-- ---------------------------------------------------------------------
-- 되돌리기 — 🔴 **무손실이 아니다.**
--
--   DROP INDEX IF EXISTS pets_identity_one_per_user;
--   DROP INDEX IF EXISTS idx_pets_identity;
--   ALTER TABLE pets DROP COLUMN IF EXISTS identity_id;
--   DROP TABLE IF EXISTS pet_identities;
--
-- 기존 pet 행과 케어·산책 기록은 한 줄도 안 건드렸으므로 그대로 남는다. 그러나
-- **연결 관계 자체는 이 표와 이 칸에만 있다.** 기능이 한 번이라도 쓰인 뒤에 지우면
-- 사용자가 맺은 연결이 전부 사라지고, 되살리려면 사람이 다시 초대·수락해야 한다.
-- 적용 전으로 돌아가는 것이 아니라 **적용 후 생긴 것을 버리는 것**이다.
--
-- 지워야 한다면 그 전에 따로 떠 둔다:
--   pg_dump -Fc -t pet_identities -t pets <db> > before-drop.dump
--
-- 되돌리기의 1순위는 이것이 아니라 **코드 롤백(스키마 유지)** 이다. 자세한 것과
-- 그때 같이 되돌아가는 규칙들은 docs/co-care.md 의 "롤백" 절에 있다.
-- ---------------------------------------------------------------------
