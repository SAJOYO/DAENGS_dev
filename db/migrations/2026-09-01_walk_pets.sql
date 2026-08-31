-- 2026-09-01_walk_pets.sql
-- 산책에 강아지를 여러 마리 붙인다. db/init/06_walks.sql 과 같은 결과가 되게 한다.
--
-- **여러 번 돌려도 안전하다.** 이 저장소는 버전 테이블이 없어서 DB 가 적용 여부를
-- 기억하지 않는다 — db/migrations/README.md 참고.
--
-- 왜: 한 번에 두 마리를 데리고 나가는데 walks.pet_id 는 한 칸이라 한 아이의 기록만
-- 남았다. 나중에 챗봇이 "이 아이 이번 주 운동량"을 말하려면 그때 그 아이가 나갔는지가
-- 남아 있어야 한다.
--
-- **이미 걸어서 쌓인 행이 있다.** 컬럼을 지우기 전에 값을 옮긴다.

BEGIN;

CREATE TABLE IF NOT EXISTS walk_pets (
    walk_id UUID NOT NULL REFERENCES walks(id) ON DELETE CASCADE,

    -- 단수 pet_id 일 때는 SET NULL 이었다. 여기서는 CASCADE 지만 뜻은 같다 —
    -- 강아지를 지우면 이 연결만 사라지고 **산책 자체는 남는다.**
    pet_id UUID NOT NULL REFERENCES pets(id) ON DELETE CASCADE,

    PRIMARY KEY (walk_id, pet_id)
);

CREATE INDEX IF NOT EXISTS walk_pets_pet_idx ON walk_pets (pet_id);

-- 있던 값을 옮긴다. 컬럼이 이미 없으면(두 번째 실행) 이 블록은 건너뛴다.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'walks' AND column_name = 'pet_id'
    ) THEN
        INSERT INTO walk_pets (walk_id, pet_id)
        SELECT id, pet_id FROM walks WHERE pet_id IS NOT NULL
        ON CONFLICT DO NOTHING;
    END IF;
END $$;

ALTER TABLE walks DROP COLUMN IF EXISTS pet_id;

COMMIT;
