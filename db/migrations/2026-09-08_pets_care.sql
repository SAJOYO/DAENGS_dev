-- 2026-09-08_pets_care.sql
-- 강아지에게 **돌봄 칸 넷**을 더한다 — 급식 방식 · 급식 시각 · 앓는 병 · 정기 복용 약.
-- db/init/05_pets.sql 과 같은 결과가 되게 한다.
--
-- **여러 번 돌려도 안전하다.** 이 저장소는 버전 테이블이 없어서 DB 가 적용 여부를
-- 기억하지 않는다 — db/migrations/README.md 참고. 제약은 지우고 다시 건다.
--
-- 왜: 기획이 "개인비서"인데 비서가 이 아이의 지병·급식 방식을 모른 채 통상 기준으로
-- 답한다(#331). `pets → services/dog_context → DogContext → DOG_CONTEXT` 배관은 B4 가
-- 이미 놓았고, 여기서는 그 배관에 태울 값을 받을 자리만 만든다.
--
-- **옛 앱은 그대로 돈다.** 넷 다 NULL 허용이고 기본값이 없어서, 이 칸을 모르는 앱이
-- 보내는 PUT 은 NULL 로 남는다.

BEGIN;

ALTER TABLE pets ADD COLUMN IF NOT EXISTS feeding_style VARCHAR(10);
ALTER TABLE pets ADD COLUMN IF NOT EXISTS feeding_times JSONB;
ALTER TABLE pets ADD COLUMN IF NOT EXISTS health_conditions VARCHAR(200);
ALTER TABLE pets ADD COLUMN IF NOT EXISTS medications VARCHAR(200);

COMMENT ON COLUMN pets.feeding_style IS
    '급식 방식. free 자율급식 · scheduled 시간제. NULL 은 ''모름''이고 기본값을 걸지 않는다';
COMMENT ON COLUMN pets.feeding_times IS
    '시간제 급식 시각 JSON 배열 ["08:00","19:30"]. 시간제가 아니면 NULL';
COMMENT ON COLUMN pets.health_conditions IS
    '앓는 병. 자유 텍스트. 비서 프롬프트에 그대로 실린다';
COMMENT ON COLUMN pets.medications IS
    '정기 복용 약. 자유 텍스트. 비서 프롬프트에는 복약 여부만 간다 (약 이름은 안 간다)';

-- 제약 셋. `ADD CONSTRAINT IF NOT EXISTS` 가 없어서 지우고 다시 건다 — 재실행 안전.
ALTER TABLE pets DROP CONSTRAINT IF EXISTS pets_feeding_style_check;
ALTER TABLE pets ADD CONSTRAINT pets_feeding_style_check
    CHECK (feeding_style IN ('free','scheduled'));

-- 시각은 **시간제일 때만**. 자율급식에 시각이 붙으면 어느 쪽이 맞는지 알 수 없는 행이 된다.
ALTER TABLE pets DROP CONSTRAINT IF EXISTS pets_feeding_times_need_schedule;
ALTER TABLE pets ADD CONSTRAINT pets_feeding_times_need_schedule
    CHECK (feeding_times IS NULL OR feeding_style = 'scheduled');

-- 배열이어야 한다. 객체나 문자열이 들어오면 앱이 못 읽는다.
ALTER TABLE pets DROP CONSTRAINT IF EXISTS pets_feeding_times_array;
ALTER TABLE pets ADD CONSTRAINT pets_feeding_times_array
    CHECK (feeding_times IS NULL OR jsonb_typeof(feeding_times) = 'array');

-- ⚠ **기본값을 걸지 않는다.** `feeding_style DEFAULT 'free'` 를 걸면 안 물어본 아이가
-- 전부 자율급식이 되고, 비서가 시간제 아이에게 "그릇에 늘 두시니…" 라고 답한다.
-- 옆 칸 `neutered`·`registered` 가 같은 이유로 기본값 없이 NULL 을 쓴다.
-- 이미 있는 행도 그래서 안 채운다 — 백필할 정답이 없다.
-- `verify_2026-09-08_pets_care.sql` 이 **기본값이 없는지를 단언**한다.

COMMIT;
