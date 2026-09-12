-- ---------------------------------------------------------------------
-- pet_invite_pets : 초대 묶음 (공동 돌봄 다중 초대 MVP)
-- ---------------------------------------------------------------------
-- 적용: docker compose exec -T pgvector psql -U <앱계정> -d vectordb -f - < 이 파일
--       또는 Actions 의 `DB 마이그레이션 적용`(db-migrate.yml), verify=true
--
-- ⚠ 서버 PC 터미널에서 직접 하지 말 것 — db/migrations/README.md 의 "함정 셋".
--
-- 초대 하나에 강아지 여러 마리를 담는다. **`pet_invites` 는 그대로 두고** 자식 표를
-- 더한다 — `pet_invites.pet_id` 는 앵커로 남는다:
--
--   · `idx_pet_invites_pet` · `delete_invites_for_pet`(승계) · `delete_expired_invites` ·
--     `count_valid_invites` 가 전부 그 칸을 본다
--   · NOT NULL 을 떼면 verify_2026-09-09_pet_members 와 그 변조 목록이 깨진다
--   · 구 앱의 `GET /app/pets/{pet_id}/invites` 가 그대로 돈다
--
-- **backfill 이 있다.** 기존 단일 초대는 한 마리 묶음으로 그대로 살아 있어야 한다 —
-- 마이그레이션 뒤에도 이미 뿌린 링크가 수락돼야 하기 때문이다.
--
-- 버전 테이블이 없으므로 **여러 번 돌려도 안전해야 한다** (CLAUDE.md).
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS pet_invite_pets (
    invite_id UUID NOT NULL REFERENCES pet_invites(id) ON DELETE CASCADE,
    pet_id    UUID NOT NULL REFERENCES pets(id)        ON DELETE CASCADE,

    -- 수락 때 이 강아지를 받는 사람의 **어느 pet 행에 연결했는지**. NULL 이면 연결 없이
    -- 참여다. **영수증의 일부다** — 응답을 못 받은 재시도에 그때의 매핑을 그대로 복원한다
    -- (`pet_invites.accepted_by` 만으로는 강아지별 결과를 되살릴 수 없다).
    --
    -- SET NULL 인 이유는 `accepted_by` 와 같다: 연결 대상이 훗날 진짜로 지워져도 "그때
    -- 이 사람이 받았다" 는 사실은 남아야 한다. CASCADE 면 영수증 줄이 통째로 사라진다.
    linked_pet_id UUID REFERENCES pets(id) ON DELETE SET NULL,

    PRIMARY KEY (invite_id, pet_id)
);

-- "이 아이가 낀 묶음 전부" — 구 경로 `GET /app/pets/{pet_id}/invites` 의 조회축이다.
CREATE INDEX IF NOT EXISTS idx_pet_invite_pets_pet ON pet_invite_pets (pet_id);

-- 묶음에 원래 몇 마리가 있었나.
--
-- **묶음 불변성이 이 칸에 걸려 있다** (MVP 결정 §2). 위 `pet_id` 의 CASCADE 때문에
-- 강아지가 지워지면 자식 줄이 **조용히** 사라지는데, 그러면 남은 강아지만으로 부분
-- 수락이 되어 버린다. 수락할 때 자식 수와 이 값을 견줘 다르면 묶음 전체를 410 으로
-- 무효화한다 — "하나라도 무효면 전부" 가 원자성 규칙과 같은 결이다.
--
-- ⚠ **DEFAULT 1 이 꼭 있어야 한다.** 배포 순서가 마이그레이션 → 서버라(MVP 결정 §9)
--   그 사이에는 **옛 서버 코드가 새 스키마에 INSERT** 한다. 기본값이 없으면 그 창 동안
--   초대 생성이 NOT NULL 위반으로 통째로 죽는다. 옛 코드는 한 마리 초대만 만드므로 1 이
--   정확한 값이다.
ALTER TABLE pet_invites ADD COLUMN IF NOT EXISTS pet_count SMALLINT DEFAULT 1;

-- backfill — 기존 초대는 전부 한 마리 묶음이다.
INSERT INTO pet_invite_pets (invite_id, pet_id)
SELECT id, pet_id FROM pet_invites
ON CONFLICT DO NOTHING;

UPDATE pet_invites SET pet_count = 1 WHERE pet_count IS NULL;

ALTER TABLE pet_invites ALTER COLUMN pet_count SET NOT NULL;

-- 빈 묶음은 뜻이 없다. 상한(5)은 제품 상수라 여기 안 박는다 — 바뀌면 마이그레이션이
-- 또 필요해진다. 하한만 DB 가 지킨다.
DO $pet_count_check$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = to_regclass('pet_invites')
          AND conname = 'pet_invites_pet_count_check'
    ) THEN
        ALTER TABLE pet_invites
            ADD CONSTRAINT pet_invites_pet_count_check CHECK (pet_count >= 1);
    END IF;
END
$pet_count_check$;

COMMENT ON TABLE pet_invite_pets IS
    '초대 묶음에 담긴 강아지와 수락 때의 연결 결과 (docs/co-care.md)';
COMMENT ON COLUMN pet_invites.pet_count IS
    '묶음의 원래 마릿수. 자식 줄 수와 다르면 구성이 바뀐 것이라 수락을 410 으로 막는다';

-- ---------------------------------------------------------------------
-- 되돌리기 (데이터 손실 없음 — 묶음과 영수증 매핑만 사라진다)
--
--   ALTER TABLE pet_invites DROP CONSTRAINT IF EXISTS pet_invites_pet_count_check;
--   ALTER TABLE pet_invites DROP COLUMN IF EXISTS pet_count;
--   DROP TABLE IF EXISTS pet_invite_pets;
--
-- `pet_invites.pet_id` 가 앵커로 남아 있어 기존 단일 초대는 그대로 동작한다.
-- ---------------------------------------------------------------------
