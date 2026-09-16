-- ---------------------------------------------------------------------
-- ai_card_usage : AI 도감 카드 하루 한도를 세는 사용 기록 (#543, D-077, docs/cardimage/)
-- ---------------------------------------------------------------------
-- 카드가 'ready' 가 되는 순간 backend 가 한 줄 남긴다. 하루 한도는 이 표의 KST 오늘 사용 기록 줄 수다.
--
-- ⚠️ **card_id 에 FK 를 걸지 않는다.** 카드를 지워도 이 줄은 남아야 한다 — "지워도 횟수는
--    돌아오지 않는다"(사용자 결정 2026-09-15). FK 를 걸면 CASCADE 는 횟수를 돌려주고,
--    RESTRICT 는 카드 삭제를 막는다.
-- ⚠️ **CASCADE 에 기대면 안 된다.** 탈퇴는 app_users 행을 남기므로 이 CASCADE 는 영영 안 돈다 —
--    탈퇴 경로(services/ai_card.py::cleanup_for_owner)가 명시로 지운다 — 단 KST 오늘 기록은 남긴다.
--    같은 카카오 계정으로 재로그인하면 같은 app_user_id 라, 오늘 기록을 지우면 그날 한도가 초기화된다.
--
-- #572(D-084) 부터 **한 요청(pick_group)에 한 줄**이다.
--   1. 요청의 첫 카드가 슬롯을 잡는 트랜잭션에서(**유료 호출 전에**) 시도 표시를 남긴다
--      (unfulfilled_attempt = true, card_id = 그 요청의 pick_group).
--   2. 닮음이 기준(DAENGS_CARDIMAGE_JUDGE_MIN) 이상인 카드가 처음 ready 가 되면 같은 트랜잭션에서
--      그 표시를 지우고 사용 기록(unfulfilled_attempt = false, card_id = 그 카드)을 남긴다.
--   3. 좋은 카드가 끝내 안 나온 요청 — 닮음 미달 · 호출 실패 · 생성 중 삭제 — 은 표시가 그대로 남는다.
-- 하루 한도는 false 줄만 세고, true 줄은 돈 나간 시도 상한(services/ai_card_quota.py::
-- MAX_PAID_FAILURES_PER_DAY)이 센다. 카드 행이 아니라 이 표에 두는 이유는 같다 — 카드를 지워도
-- 표시는 남아야 한다.

CREATE TABLE IF NOT EXISTS ai_card_usage (
    -- 한 요청에 기록 하나. 백필을 여러 번 돌려도 ON CONFLICT 로 같은 결과가 된다.
    card_id UUID PRIMARY KEY,
    app_user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    used_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- 기본 false — 옛 줄과 백필 줄은 전부 사용 기록이다. true 가 기본이면 하루 한도가 아무것도 안 센다.
    unfulfilled_attempt BOOLEAN NOT NULL DEFAULT FALSE
);

-- 한도는 늘 "내 오늘 기록 수".
CREATE INDEX IF NOT EXISTS idx_ai_card_usage_owner_used
    ON ai_card_usage (app_user_id, used_at DESC);
