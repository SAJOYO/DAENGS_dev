-- #572 Task 4 — 한 요청에서 나온 카드들을 묶는다(pick_group) + 「사용자별 동시 1장」을 그
-- 요청의 대표 행(id = pick_group) 하나로 좁힌다(fix round 1 Critical). **한 트랜잭션으로
-- 묶는다** — 컬럼 추가와 인덱스 전환이 별개 파일·별개 커밋으로 갈라지면, 적용 경로
-- (`psql -X -v ON_ERROR_STOP=1`, `-1`/단일 트랜잭션 없음)에서 옛 인덱스가 이미 지워진 뒤
-- 새 인덱스가 (아직 없는 pick_group 컬럼 때문에) 실패해 ai_cards 에 「동시 1장」을 막는
-- 인덱스가 하나도 없는 채로 남을 수 있었다 — 게다가 파일 이름 정렬(`generating_leader` <
-- `pick_group`, `g` < `p`)이 실제로 그 순서로 적용되게 만들었다(fix round 2 R2-1). 여러 번
-- 돌려도 안전하다.
BEGIN;

ALTER TABLE ai_cards ADD COLUMN IF NOT EXISTS pick_group UUID;

-- 「고른 카드만 남기고 형제를 지운다」 가 pick_group 으로 형제를 찾을 때 쓴다.
CREATE INDEX IF NOT EXISTS ix_ai_cards_pick_group ON ai_cards (pick_group);

-- 형제 행(id <> pick_group)은 이제 이 인덱스가 보지 않는다 — 한 요청의 여러 행이 동시에
-- generating 이어도 된다. 대표 행(id = pick_group)만 사용자별로 유일해야 한다.
DROP INDEX IF EXISTS idx_ai_cards_one_generating;
CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_cards_one_generating
    ON ai_cards (app_user_id) WHERE status = 'generating' AND id = pick_group;

COMMIT;
