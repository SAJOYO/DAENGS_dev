-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — 이유는 verify_2026-09-05_app_user_nickname.sql 머리말과 같다.
--
-- 한 칸 + 인덱스 하나짜리 마이그레이션이다(#572 Task 4). pick_group 은 nullable 이어야 한다 —
-- 단일 카드로 만들어진 옛 행·관리자 콘솔 카드는 묶을 형제가 없어 NULL 이다.
DO $verify$
DECLARE
    relation regclass;
    definition text;
BEGIN
    IF to_regclass('ai_cards') IS NULL THEN
        RAISE EXCEPTION 'missing table: ai_cards';
    END IF;
    relation := to_regclass('ai_cards');

    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute a
        WHERE a.attrelid = relation AND a.attname = 'pick_group'
          AND a.attnum > 0 AND NOT a.attisdropped
          AND format_type(a.atttypid, a.atttypmod) = 'uuid'
          AND NOT a.attnotnull
    ) THEN
        RAISE EXCEPTION 'column mismatch: ai_cards.pick_group (want uuid, nullable)';
    END IF;

    -- 「고른 카드만 남기고 형제를 지운다」 가 이 인덱스로 같은 pick_group 행을 찾는다.
    -- UNIQUE 가 아니어야 한다 — 같은 pick_group 값을 여러 행이 공유하는 것이 정상이다.
    definition := NULL;
    SELECT pg_get_indexdef(i.indexrelid) INTO definition
    FROM pg_index i
    JOIN pg_class c ON c.oid = i.indexrelid
    WHERE i.indrelid = relation AND c.relname = 'ix_ai_cards_pick_group'
      AND i.indisvalid AND i.indisready AND NOT i.indisunique;
    IF definition IS NULL THEN
        RAISE EXCEPTION 'index mismatch: ix_ai_cards_pick_group missing, invalid, or unexpectedly unique';
    END IF;
END
$verify$;

-- ─────────────────────────────────────────────────────────────────────────
-- 사람이 눈으로 보는 자리. 위 단언이 통과한 뒤에만 여기까지 온다.
-- ─────────────────────────────────────────────────────────────────────────

SELECT count(*) AS cards_total, count(pick_group) AS with_pick_group FROM ai_cards;
