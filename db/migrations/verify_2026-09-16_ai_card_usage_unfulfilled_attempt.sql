-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — 이유는 verify_2026-09-05_app_user_nickname.sql 머리말과 같다.
--
-- 한 칸짜리 마이그레이션이다(#572 Task 5, D-084). unfulfilled_attempt 는 boolean · NOT NULL ·
-- 기본 false 여야 한다. 기본값이 없거나 true 면 사용 기록을 적는 쪽이 이 칸을 빠뜨리는 순간
-- (옛 코드) 그 줄이 시도 표시가 되거나 INSERT 가 죽는다 — 하루 한도가 아무것도 안 세게 되거나
-- 카드 완료 기록이 실패한다.
DO $verify$
DECLARE
    relation regclass;
BEGIN
    IF to_regclass('ai_card_usage') IS NULL THEN
        RAISE EXCEPTION 'missing table: ai_card_usage';
    END IF;
    relation := to_regclass('ai_card_usage');

    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute a
        WHERE a.attrelid = relation AND a.attname = 'unfulfilled_attempt'
          AND a.attnum > 0 AND NOT a.attisdropped
          AND format_type(a.atttypid, a.atttypmod) = 'boolean'
          AND a.attnotnull
    ) THEN
        RAISE EXCEPTION 'column mismatch: ai_card_usage.unfulfilled_attempt (want boolean, not null)';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute a
        JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE a.attrelid = relation AND a.attname = 'unfulfilled_attempt'
          AND pg_get_expr(d.adbin, d.adrelid) = 'false'
    ) THEN
        RAISE EXCEPTION 'column mismatch: ai_card_usage.unfulfilled_attempt default must be false';
    END IF;
END
$verify$;

-- ─────────────────────────────────────────────────────────────────────────
-- 사람이 눈으로 보는 자리. 위 단언이 통과한 뒤에만 여기까지 온다.
-- ─────────────────────────────────────────────────────────────────────────

SELECT unfulfilled_attempt, count(*) AS rows FROM ai_card_usage GROUP BY unfulfilled_attempt ORDER BY unfulfilled_attempt;
