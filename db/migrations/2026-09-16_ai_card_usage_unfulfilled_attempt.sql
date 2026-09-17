-- #572 Task 5 (D-084) — 「유료 호출까지 갔는데 아직 좋은 카드가 안 나온 요청」 표시 칸.
-- ai_card_usage 한 줄이 하루 한도가 세는 사용 기록인지(false), 돈 나간 시도 상한이 세는 시도
-- 표시인지(true)를 가른다 — 규칙은 db/init/39_ai_card_usage.sql 머리말. 표시는 요청의 첫 슬롯을
-- 잡는 트랜잭션에서(유료 호출 전에) 남고, 기준 이상 카드가 나오면 사용 기록으로 바뀐다. 카드를
-- 지워도 남는다 — 생성 중 삭제·실패·닮음 미달 요청이 모두 이 한 줄로 세진다.
--
-- 옛 줄은 전부 사용 기록이었으므로 DEFAULT FALSE 가 곧 백필이다. 사용 기록을 적는 쪽이 이 칸을
-- 안 적어도(옛 코드) 같은 기본값이 필요하다.
--
-- 배포 순서: 이 파일을 코드보다 먼저 적용한다 (DB 먼저, 코드 나중). 옛 코드는 이 칸을 모르고
-- 기본값 false 로 들어가 지금처럼 돈다. 여러 번 돌려도 안전하다.
BEGIN;

ALTER TABLE ai_card_usage ADD COLUMN IF NOT EXISTS unfulfilled_attempt BOOLEAN NOT NULL DEFAULT FALSE;

COMMIT;
