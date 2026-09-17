-- 이미 돌고 있는 DB 에 적용 (db/init/39_ai_card_usage.sql 과 같은 표 + 기존 카드 백필, 여러 번 돌려도 안전).
-- ai_card_usage : AI 도감 카드 하루 한도를 세는 사용 기록 (#543, D-077, docs/cardimage/)
-- ---------------------------------------------------------------------
-- 카드가 'ready' 가 되는 순간 backend 가 한 줄 남긴다. 하루 한도는 이 표의 KST 오늘 줄 수다.
--
-- ⚠️ **card_id 에 FK 를 걸지 않는다.** 카드를 지워도 이 줄은 남아야 한다.
-- ⚠️ **CASCADE 에 기대면 안 된다.** 탈퇴 경로가 명시로 지운다(KST 오늘 기록은 남겨 재로그인으로 한도가 초기화되지 않게).
--
-- 배포 순서: **이 파일을 코드보다 먼저** 적용한다 (DB 먼저, 코드 나중).
--
-- **이미 적용된 파일을 고쳤다 (#572 Task 5 fix round 1, D-084).** 처음 판은 백필 INSERT 를 매번
-- 돌렸다. #572 부터 「ready 카드마다 자기 id 로 사용 기록 한 줄」이 참이 아니다 — 한 요청의 둘째
-- 카드는 줄이 없고, 좋은 카드가 나온 요청은 그 카드 id 로 한 줄, 닮음 미달·생성 중 삭제 요청은
-- 사용 기록 대신 시도 표시(unfulfilled_attempt, card_id = pick_group)만 남는다. 그래서 운영 DB 에
-- 이 파일을 **다시** 돌리면 줄 없는 ready 카드마다 사용 기록이 새로 생겨, 오늘 미달이었던 요청이
-- 하루 한도를 먹고(한도 1 이면 거절) 좋은 요청은 두 줄이 됐다.
-- 고친 판은 **표를 이 실행에서 만들 때만** 백필한다(아래 DO 블록). 이미 적용된 DB 에서는 표가
-- 있으므로 DO 블록이 아무것도 안 하고, 인덱스는 IF NOT EXISTS 라 그대로다 — **끝 상태는 처음 판을
-- 적용한 DB 와 같고, 다시 돌리면 아무것도 안 바뀐다.** 새 DB 에서는 처음 판과 똑같이 만들고 백필한다.
BEGIN;

DO $migrate$
BEGIN
    IF to_regclass('ai_card_usage') IS NULL THEN
        CREATE TABLE ai_card_usage (
            card_id UUID PRIMARY KEY,
            app_user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
            used_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        -- 백필: 지금 남아 있는 'ready' 카드마다 한 줄. 안 옮기면 배포 당일 이미 만든 사람이 한 장 더 만든다.
        -- used_at 은 ready 가 된 시각에 가장 가까운 updated_at. 이미 지운 카드는 기록할 방법이 없다.
        -- 표를 방금 만들었으므로 여기 있는 카드는 전부 #572 이전(요청 묶음이 없던) 카드다.
        INSERT INTO ai_card_usage (card_id, app_user_id, used_at)
        SELECT id, app_user_id, updated_at FROM ai_cards WHERE status = 'ready'
        ON CONFLICT (card_id) DO NOTHING;
    END IF;
END
$migrate$;

CREATE INDEX IF NOT EXISTS idx_ai_card_usage_owner_used
    ON ai_card_usage (app_user_id, used_at DESC);

COMMIT;
