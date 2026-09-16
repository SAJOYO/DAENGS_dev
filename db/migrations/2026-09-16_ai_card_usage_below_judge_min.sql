-- #572 Task 5 (D-084) — 닮음 기준 미만 카드만 나온 요청을 「카드를 지워도 안 사라지는」 표시로
-- 남긴다. ai_card_usage 한 줄이 하루 한도가 세는 사용 기록인지(false), 돈 나간 헛시도 상한이 세는
-- 미달 표시인지(true)를 가르는 칸이다 — 규칙은 db/init/39_ai_card_usage.sql 머리말.
--
-- 옛 줄은 전부 사용 기록이었으므로 DEFAULT FALSE 가 곧 백필이다. 2026-09-15_ai_card_usage 의
-- 백필 INSERT 를 다시 돌려도 이 칸을 안 적으므로 같은 기본값이 필요하다.
--
-- 배포 순서: 이 파일을 코드보다 먼저 적용한다 (DB 먼저, 코드 나중). 옛 코드는 이 칸을 모르고
-- 기본값 false 로 들어가 지금처럼 돈다. 여러 번 돌려도 안전하다.
BEGIN;

ALTER TABLE ai_card_usage ADD COLUMN IF NOT EXISTS below_judge_min BOOLEAN NOT NULL DEFAULT FALSE;

COMMIT;
