-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — SELECT 나열이면 틀려도 종료 코드 0 이라 녹색이 된다 (#273 · #292).
DO $verify$
DECLARE
    item record;
    relation regclass;
    definition text;
    missing_backfill boolean;
BEGIN
    IF to_regclass('ai_card_usage') IS NULL THEN
        RAISE EXCEPTION 'missing table: ai_card_usage';
    END IF;
    relation := to_regclass('ai_card_usage');

    FOR item IN SELECT * FROM (VALUES
        ('card_id', 'uuid', 'true'),
        ('app_user_id', 'uuid', 'true'),
        ('used_at', 'timestamp with time zone', 'true')
    ) AS expected(column_name, type_name, required) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = relation AND a.attname = item.column_name
              AND a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) = item.type_name
              AND a.attnotnull = item.required::boolean
        ) THEN
            RAISE EXCEPTION 'column mismatch: ai_card_usage.% (type %, not null %)',
                item.column_name, item.type_name, item.required;
        END IF;
    END LOOP;

    -- PK 는 card_id, FK 는 app_user_id → app_users CASCADE 하나뿐.
    FOR item IN SELECT * FROM (VALUES
        ('p', 'card_id', NULL, NULL, NULL),
        ('f', 'app_user_id', 'app_users', 'id', 'c')
    ) AS expected(kind, columns, target_table, target_columns, delete_action) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint c
            WHERE c.conrelid = relation AND c.contype::text = item.kind
              AND c.convalidated
              AND ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(num, pos)
                        JOIN pg_attribute a ON a.attrelid = relation AND a.attnum = k.num
                        ORDER BY k.pos) = string_to_array(item.columns, ',')
              AND (item.kind <> 'f' OR (
                  c.confrelid = to_regclass(item.target_table)
                  AND c.confdeltype::text = item.delete_action
                  AND ARRAY(SELECT a.attname::text FROM unnest(c.confkey) WITH ORDINALITY k(num, pos)
                            JOIN pg_attribute a ON a.attrelid = c.confrelid AND a.attnum = k.num
                            ORDER BY k.pos) = string_to_array(item.target_columns, ',')
              ))
        ) THEN
            RAISE EXCEPTION 'constraint mismatch: ai_card_usage kind % columns %',
                item.kind, item.columns;
        END IF;
    END LOOP;

    -- **card_id 에 FK 가 생기면 안 된다** — 카드를 지울 때 기록이 같이 사라져 횟수가 돌아온다.
    IF (SELECT count(*) FROM pg_constraint c WHERE c.conrelid = relation AND c.contype = 'f') <> 1 THEN
        RAISE EXCEPTION 'constraint mismatch: ai_card_usage must have exactly one FK (app_user_id)';
    END IF;

    definition := NULL;
    SELECT pg_get_indexdef(i.indexrelid) INTO definition
    FROM pg_index i
    JOIN pg_class c ON c.oid = i.indexrelid
    WHERE i.indrelid = relation AND c.relname = 'idx_ai_card_usage_owner_used'
      AND i.indisvalid AND i.indisready AND NOT i.indisunique;
    IF definition IS NULL THEN
        RAISE EXCEPTION 'index mismatch: idx_ai_card_usage_owner_used missing, invalid, or unique';
    END IF;

    -- 백필: 남아 있는 **옛(#572 이전) ready 카드**에는 전부 자기 id 의 기록이 있어야 한다.
    -- #572 부터(D-084) 요청 묶음(pick_group)이 있는 카드는 이 규칙이 아니다 — 한 요청에 기록이
    -- 한 줄뿐이고(좋은 카드 id 또는 pick_group 의 시도 표시), 둘째 카드에는 줄이 없다. 그래서
    -- pick_group 이 없는 카드만 본다. 새 DB 에서는 이 파일이 pick_group 을 만드는 2026-09-16 파일보다
    -- **먼저** 돌므로 칸이 아직 없을 수 있다 — 그때는 모든 카드가 옛 카드다. 칸에 기대는 쿼리는
    -- 칸이 없으면 계획 단계에서 죽으므로 EXECUTE 로 부른다.
    IF EXISTS (
        SELECT 1 FROM pg_attribute a
        WHERE a.attrelid = to_regclass('ai_cards') AND a.attname = 'pick_group'
          AND a.attnum > 0 AND NOT a.attisdropped
    ) THEN
        EXECUTE 'SELECT EXISTS (SELECT 1 FROM ai_cards c WHERE c.status = ''ready'' AND c.pick_group IS NULL'
                ' AND NOT EXISTS (SELECT 1 FROM ai_card_usage u WHERE u.card_id = c.id))'
            INTO missing_backfill;
    ELSE
        EXECUTE 'SELECT EXISTS (SELECT 1 FROM ai_cards c WHERE c.status = ''ready'''
                ' AND NOT EXISTS (SELECT 1 FROM ai_card_usage u WHERE u.card_id = c.id))'
            INTO missing_backfill;
    END IF;
    IF missing_backfill THEN
        RAISE EXCEPTION 'backfill mismatch: legacy ready ai_cards without ai_card_usage rows';
    END IF;
END
$verify$;

-- 사람이 눈으로 보는 자리. 새 DB 에 적용한 직후에는 ready 카드 수와 같다(#572 뒤로는 같지 않다).
SELECT count(*) AS usage_rows FROM ai_card_usage;
