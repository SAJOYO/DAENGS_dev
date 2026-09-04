-- 2026-09-02_pet_farewell.sql
-- 강아지에게 **배웅한 날** 한 칸을 더한다.
-- db/init/05_pets.sql 과 같은 결과가 되게 한다.
--
-- **여러 번 돌려도 안전하다.** 이 저장소는 버전 테이블이 없어서 DB 가 적용 여부를
-- 기억하지 않는다 — db/migrations/README.md 참고.
--
-- 왜: 앱에 배웅하기가 생겼다(SAJOYO/DAENGS_APP#76). 아이를 떠나보낸 사람에게 선택지가
-- 삭제뿐이던 것을 고친 것이고, 간 날을 적어 두면 아이는 목록에 남고 함께한 산책과
-- 카드도 남는다. 그런데 pets 에 그 칸이 없어서 지금은 기기에만 적힌다 — 기기를 바꾸거나
-- 앱을 지우면 사라진다는 뜻이고, 아이를 잃은 기록이 그렇게 사라지면 안 된다.
--
-- 삭제와 다른 일이라 행을 안 지우고 날짜만 채운다.

BEGIN;

ALTER TABLE pets ADD COLUMN IF NOT EXISTS farewell_on DATE;

COMMENT ON COLUMN pets.farewell_on IS
    '배웅한 날. NULL 이면 아직 함께 있는 아이다. 삭제와 다른 일이라 행을 안 지운다';

-- 앞날은 못 넣는다. 오타 한 자로 2033년이 적히면 "아직 안 온 날에 배웅했다"가 된다.
--
-- **CURRENT_DATE 를 쓰는 CHECK 는 IMMUTABLE 이 아니다.** Postgres 가 CHECK 안의
-- 비결정 함수를 막지는 않지만, 이미 들어간 행을 나중에 다시 검사하지 않으므로
-- "넣을 때만" 보는 규칙으로 읽어야 한다. 그래도 오타를 거르는 데는 그걸로 충분하다.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'pets_farewell_not_future'
    ) THEN
        ALTER TABLE pets ADD CONSTRAINT pets_farewell_not_future
            CHECK (farewell_on IS NULL OR farewell_on <= CURRENT_DATE);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'pets_farewell_after_birth'
    ) THEN
        ALTER TABLE pets ADD CONSTRAINT pets_farewell_after_birth
            CHECK (farewell_on IS NULL OR birth_date IS NULL OR farewell_on >= birth_date);
    END IF;
END $$;

COMMIT;
