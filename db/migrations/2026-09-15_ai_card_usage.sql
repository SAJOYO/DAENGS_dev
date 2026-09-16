-- 이미 돌고 있는 DB 에 적용 (db/init/39_ai_card_usage.sql 과 같은 표 + 기존 카드 백필, 여러 번 돌려도 안전).
-- ai_card_usage : AI 도감 카드 하루 한도를 세는 사용 기록 (#543, D-077, docs/cardimage/)
-- ---------------------------------------------------------------------
-- 카드가 'ready' 가 되는 순간 backend 가 한 줄 남긴다. 하루 한도는 이 표의 KST 오늘 줄 수다.
--
-- ⚠️ **card_id 에 FK 를 걸지 않는다.** 카드를 지워도 이 줄은 남아야 한다.
-- ⚠️ **CASCADE 에 기대면 안 된다.** 탈퇴 경로가 명시로 지운다(KST 오늘 기록은 남겨 재로그인으로 한도가 초기화되지 않게).
--
-- 배포 순서: **이 파일을 코드보다 먼저** 적용한다 (DB 먼저, 코드 나중).

CREATE TABLE IF NOT EXISTS ai_card_usage (
    card_id UUID PRIMARY KEY,
    app_user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    used_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ai_card_usage_owner_used
    ON ai_card_usage (app_user_id, used_at DESC);

-- 백필: 지금 남아 있는 'ready' 카드마다 한 줄. 안 옮기면 배포 당일 이미 만든 사람이 한 장 더 만든다.
-- used_at 은 ready 가 된 시각에 가장 가까운 updated_at. 이미 지운 카드는 기록할 방법이 없다.
INSERT INTO ai_card_usage (card_id, app_user_id, used_at)
SELECT id, app_user_id, updated_at FROM ai_cards WHERE status = 'ready'
ON CONFLICT (card_id) DO NOTHING;
