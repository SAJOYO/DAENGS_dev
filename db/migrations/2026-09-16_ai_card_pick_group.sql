-- #572 Task 4 — 한 요청에서 나온 카드들을 묶는다. 여러 번 돌려도 안전하다.
ALTER TABLE ai_cards ADD COLUMN IF NOT EXISTS pick_group UUID;
CREATE INDEX IF NOT EXISTS ix_ai_cards_pick_group ON ai_cards (pick_group);
