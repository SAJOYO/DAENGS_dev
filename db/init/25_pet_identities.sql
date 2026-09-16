-- ---------------------------------------------------------------------
-- pet_identities : 논리 강아지 (공동 돌봄 다중 초대, docs/co-care.md)
-- ---------------------------------------------------------------------
-- Order: … -> 05_pets -> 24_pet_members -> 25_pet_identities
--
-- 여러 `pets` 행이 **같은 실제 강아지**라는 관계다. 물리 병합이 아니다 — 기존 행도
-- 13개 표의 `pet_id` 기록도 옮기거나 지우지 않는다. 관계만 더하고, 목록과 공동
-- 조회에서 한 마리처럼 보이게 한다.
--
-- `identity_id IS NULL` 이 "연결 안 된 보통 강아지" 다. 마릿수 상한이
-- `COUNT(DISTINCT COALESCE(identity_id, id))` 라, 연결이 없으면 지금과 같은 수를 센다.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pet_identities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- 그룹의 주보호자를 **사람이 아니라 대표 pet 행**으로 가리킨다.
    -- `pet_members` 에 role 칸을 안 둔 것과 같은 이유다: 주보호자의 원본은
    -- `pets.app_user_id` 하나뿐이라, 여기에 사람 id 를 또 두면 승계 때 두 곳이 어긋난다.
    --
    -- CASCADE — 앵커 pet 행이 지워지면 그룹도 뜻을 잃는다. 그때 아래 `identity_id` 의
    -- SET NULL 이 남은 행들을 독립 강아지로 되돌린다. **기록은 그대로 남는다.**
    owner_pet_id UUID NOT NULL REFERENCES pets(id) ON DELETE CASCADE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 한 pet 행이 두 그룹의 앵커일 수 없다.
CREATE UNIQUE INDEX IF NOT EXISTS pet_identities_owner_pet
    ON pet_identities (owner_pet_id);

-- SET NULL — 그룹이 사라져도 pet 행은 남아야 한다. CASCADE 로 바꾸면 그룹 행 하나
-- 때문에 사람들의 강아지와 기록이 통째로 사라진다.
ALTER TABLE pets ADD COLUMN IF NOT EXISTS identity_id UUID
    REFERENCES pet_identities(id) ON DELETE SET NULL;

-- "이 그룹의 pet 행 전부" 가 케어·산책 공동 조회의 첫 걸음이다. 그 조회가 제일 잦다.
CREATE INDEX IF NOT EXISTS idx_pets_identity ON pets (identity_id);

-- **한 사람은 같은 그룹에 pet 행을 둘 이상 가질 수 없다.** 기존 강아지 하나를 초대
-- 강아지 두 마리에 연결하는 것을 DB 가 거절하는 자리이고, 목록 접기가 항상 카드
-- 한 장을 내놓는 근거다. 부분 인덱스인 이유는 연결 안 된 행이 전부 NULL 이라서다.
CREATE UNIQUE INDEX IF NOT EXISTS pets_identity_one_per_user
    ON pets (identity_id, app_user_id) WHERE identity_id IS NOT NULL;

COMMENT ON TABLE pet_identities IS
    '논리 강아지. 여러 pets 행이 같은 실제 강아지임을 나타낸다 (docs/co-care.md)';
COMMENT ON COLUMN pets.identity_id IS
    'NULL 이면 연결 안 된 보통 강아지. 같은 값이면 같은 실제 강아지다';
