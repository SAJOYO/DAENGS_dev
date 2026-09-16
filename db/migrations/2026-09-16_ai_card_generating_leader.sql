-- #572 Task 4 fix round 1 (Critical) — 한 요청에서 여러 장이 한꺼번에 generating 일 수 있어,
-- 「사용자별 동시 1장」 제약을 그 요청의 대표 행(id = pick_group) 하나로 좁힌다. 형제 행
-- (id <> pick_group)은 이제 이 인덱스가 보지 않는다 — 여러 개가 동시에 generating 이어도 된다.
-- 여러 번 돌려도 안전하다.
DROP INDEX IF EXISTS idx_ai_cards_one_generating;
CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_cards_one_generating
    ON ai_cards (app_user_id) WHERE status = 'generating' AND id = pick_group;
