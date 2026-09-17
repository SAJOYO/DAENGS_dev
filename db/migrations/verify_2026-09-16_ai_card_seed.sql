-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — 이유는 verify_2026-09-05_app_user_nickname.sql 머리말과 같다.
--
-- 한 칸짜리 마이그레이션이다. seed 는 SmallInteger 로 좁아지면 안 된다(#572 Task 3a) —
-- 카드 생성기가 32767 을 넘는 seed 를 쓸 수 있어서, 타입이 좁아지면 조용히 잘리거나 에러가 난다.
DO $verify$
DECLARE
    relation regclass;
BEGIN
    IF to_regclass('ai_cards') IS NULL THEN
        RAISE EXCEPTION 'missing table: ai_cards';
    END IF;
    relation := to_regclass('ai_cards');

    -- ① 칸이 있고 ② integer · nullable 이어야 한다. 기존 카드에는 값이 없으므로 NOT NULL 이
    --    걸리면 이 ALTER 를 여러 번 돌릴 때 옛 행이 전부 막힌다.
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute a
        WHERE a.attrelid = relation AND a.attname = 'seed'
          AND a.attnum > 0 AND NOT a.attisdropped
          AND format_type(a.atttypid, a.atttypmod) = 'integer'
          AND NOT a.attnotnull
    ) THEN
        RAISE EXCEPTION 'column mismatch: ai_cards.seed (want integer, nullable)';
    END IF;
END
$verify$;

-- ─────────────────────────────────────────────────────────────────────────
-- 사람이 눈으로 보는 자리. 위 단언이 통과한 뒤에만 여기까지 온다.
-- ─────────────────────────────────────────────────────────────────────────

SELECT count(*) AS cards_total, count(seed) AS with_seed FROM ai_cards;
