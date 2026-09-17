-- #572 Task 3 — 카드를 만든 seed 를 남긴다. 여러 번 돌려도 안전하다.
ALTER TABLE ai_cards ADD COLUMN IF NOT EXISTS seed INTEGER;
