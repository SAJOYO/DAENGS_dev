-- 이미 돌고 있는 DB 에 적용 (db/init/38_ai_cards.sql 과 같은 결과, 여러 번 돌려도 안전).
-- ai_cards 에 카드 종류 칸을 넣는다 — 달(1~12) 말고 종류 카드(딸기·상추)도 담는다 (#593, D-085).
-- ---------------------------------------------------------------------
-- 하는 일은 셋이다.
--   ① `card_key` 를 더하고 **기존 행을 `month::text` 로 백필**한 뒤 NOT NULL 로 올린다.
--   ② `month` 를 nullable 로 넓힌다 — 종류 카드에는 달이 없다.
--   ③ `ai_cards_month` CHECK 를 종류 카드가 통과하게 갈아 끼우고, 빈 `card_key` 를 막는
--      `ai_cards_card_key` 를 더한다.
--
-- **한 트랜잭션으로 묶는다.** 적용 경로(`psql -X -v ON_ERROR_STOP=1`)에 `--single-transaction`
-- 이 없어서, 중간에 죽으면 "칸은 있는데 백필이 안 된" 상태로 남을 수 있다 — 그 상태에서는
-- ③ 의 CHECK 가 붙지 않고, 앱은 NOT NULL 아닌 칸을 보게 된다 (2026-09-16 pick_group 과 같은 이유).
--
-- **여러 번 돌려도 안전하다.** 칸은 IF NOT EXISTS, 백필은 아직 비어 있는 행만(`card_key IS NULL`),
-- 제약은 DROP IF EXISTS 뒤 ADD 다. 두 번째 실행에서 백필은 0행을 고치고 제약은 같은 정의로 다시 선다.
--
-- ⚠️ **다른 칸·인덱스·제약은 하나도 건드리지 않는다** — 표를 다시 만들지 않는다.
--    `idx_ai_cards_one_generating` · `idx_ai_cards_storage_key` · `ai_cards_ready_set` 가 그대로
--    살아 있어야 한다 (verify 가 그 셋을 따로 본다).
BEGIN;

-- ① 카드 종류 칸. 처음에는 nullable 로 붙여야 기존 행이 들어온다.
ALTER TABLE ai_cards ADD COLUMN IF NOT EXISTS card_key VARCHAR(20);

-- 백필. **아직 비어 있는 행만** 고친다 — 두 번째 실행에서는 0행이고, 이미 들어와 있는
-- 종류 카드(month IS NULL)는 `month::text` 가 NULL 이라 여기서 지워질 일도 없다.
UPDATE ai_cards SET card_key = month::text WHERE card_key IS NULL AND month IS NOT NULL;

ALTER TABLE ai_cards ALTER COLUMN card_key SET NOT NULL;

-- ② 종류 카드에는 달이 없다.
ALTER TABLE ai_cards ALTER COLUMN month DROP NOT NULL;

-- ③ 옛 CHECK 는 `month BETWEEN 1 AND 12` 하나였다. 달 카드는 그대로 1~12 이고 card_key 가 그
-- 달의 문자열이어야 하며, 종류 카드는 달이 비고 card_key 가 숫자가 아니어야 한다 — 숫자를
-- 막지 않으면 "month 는 비었는데 card_key 가 '4'" 인 행이 설 수 있고, 그건 앱에 month=null 로
-- 나가는 달 카드다(전환기 계약이 month 를 함께 싣는다).
ALTER TABLE ai_cards DROP CONSTRAINT IF EXISTS ai_cards_month;
ALTER TABLE ai_cards ADD CONSTRAINT ai_cards_month CHECK (
    (month IS NOT NULL AND month BETWEEN 1 AND 12 AND card_key = month::text)
    OR (month IS NULL AND card_key !~ '^[0-9]+$')
);

ALTER TABLE ai_cards DROP CONSTRAINT IF EXISTS ai_cards_card_key;
ALTER TABLE ai_cards ADD CONSTRAINT ai_cards_card_key CHECK (length(btrim(card_key)) > 0);

COMMIT;
