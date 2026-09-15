-- ---------------------------------------------------------------------
-- ai_card_usage : AI 도감 카드 하루 한도를 세는 사용 기록 (#543, D-077, docs/cardimage/)
-- ---------------------------------------------------------------------
-- 카드가 'ready' 가 되는 순간 backend 가 한 줄 남긴다. 하루 한도는 이 표의 KST 오늘 줄 수다.
--
-- ⚠️ **card_id 에 FK 를 걸지 않는다.** 카드를 지워도 이 줄은 남아야 한다 — "지워도 횟수는
--    돌아오지 않는다"(사용자 결정 2026-09-15). FK 를 걸면 CASCADE 는 횟수를 돌려주고,
--    RESTRICT 는 카드 삭제를 막는다.
-- ⚠️ **CASCADE 에 기대면 안 된다.** 탈퇴는 app_users 행을 남기므로 이 CASCADE 는 영영 안 돈다 —
--    탈퇴 경로(services/ai_card.py::cleanup_for_owner)가 명시로 지운다.
-- 실패한 카드는 줄을 남기지 않는다 — 실패는 한도에 세지 않는다.

CREATE TABLE IF NOT EXISTS ai_card_usage (
    -- 카드 하나에 기록 하나. 백필을 여러 번 돌려도 ON CONFLICT 로 같은 결과가 된다.
    card_id UUID PRIMARY KEY,
    app_user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    used_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 한도는 늘 "내 오늘 기록 수".
CREATE INDEX IF NOT EXISTS idx_ai_card_usage_owner_used
    ON ai_card_usage (app_user_id, used_at DESC);
