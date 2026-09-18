-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — SELECT 나열이면 틀려도 종료 코드 0 이라 녹색이 된다 (#273 · #292).
DO $verify$
DECLARE
    item record;
    relation regclass;
    definition text;
BEGIN
    IF to_regclass('ai_cards') IS NULL THEN
        RAISE EXCEPTION 'missing table: ai_cards';
    END IF;
    relation := to_regclass('ai_cards');

    FOR item IN SELECT * FROM (VALUES
        ('id', 'uuid', 'true'),
        ('app_user_id', 'uuid', 'true'),
        -- 강아지를 지워도 카드는 남는다 (아래 FK 의 SET NULL 과 짝)
        ('dog_id', 'uuid', 'false'),
        -- month 는 여기 없다 — 2026-09-18 이 이 칸을 nullable 로 넓힌다(종류 카드에는 달이
        -- 없다). 아래에서 **타입은 늘, NOT NULL 은 그 장이 아직 안 온 DB 에서만** 본다.
        ('dog_name', 'character varying(40)', 'true'),
        ('title', 'character varying(80)', 'true'),
        ('status', 'character varying(16)', 'true'),
        ('error_code', 'character varying(32)', 'false'),
        ('storage_key', 'character varying(200)', 'false'),
        ('generation', 'character varying(64)', 'false'),
        ('size_bytes', 'integer', 'false'),
        ('width', 'smallint', 'false'),
        ('height', 'smallint', 'false'),
        ('likeness', 'smallint', 'false'),
        ('attempts', 'smallint', 'false'),
        ('created_at', 'timestamp with time zone', 'true'),
        ('updated_at', 'timestamp with time zone', 'true')
    ) AS expected(column_name, type_name, required) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = relation AND a.attname = item.column_name
              AND a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) = item.type_name
              AND a.attnotnull = item.required::boolean
        ) THEN
            RAISE EXCEPTION 'column mismatch: ai_cards.% (type %, not null %)',
                item.column_name, item.type_name, item.required;
        END IF;
    END LOOP;

    -- month 는 **뒤 장이 넓힌 칸**이다 (2026-09-18_ai_cards_card_key, D-085). 버전 표가 없어
    -- 이 verify 는 그 장까지 적용된 DB 위에서도 돌므로, 조건 없이 NOT NULL 을 단언하면 운영
    -- DB 전부에서 실패한다. 타입은 늘 보고, NOT NULL 은 **card_key 칸이 아직 없을 때만** 본다.
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute a
        WHERE a.attrelid = relation AND a.attname = 'month'
          AND a.attnum > 0 AND NOT a.attisdropped
          AND format_type(a.atttypid, a.atttypmod) = 'smallint'
    ) THEN
        RAISE EXCEPTION 'column mismatch: ai_cards.month (type smallint)';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute a
        WHERE a.attrelid = relation AND a.attname = 'card_key'
          AND a.attnum > 0 AND NOT a.attisdropped
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_attribute a
        WHERE a.attrelid = relation AND a.attname = 'month'
          AND a.attnum > 0 AND NOT a.attisdropped AND a.attnotnull
    ) THEN
        RAISE EXCEPTION 'column mismatch: ai_cards.month (type smallint, not null true)';
    END IF;

    -- FK 둘의 **삭제 동작이 서로 다르다**. 계정은 CASCADE, 강아지는 SET NULL.
    FOR item IN SELECT * FROM (VALUES
        ('p', 'id', NULL, NULL, NULL),
        ('f', 'app_user_id', 'app_users', 'id', 'c'),
        ('f', 'dog_id', 'pets', 'id', 'n')
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
            RAISE EXCEPTION 'constraint mismatch: ai_cards kind % columns %',
                item.kind, item.columns;
        END IF;
    END LOOP;

    FOR item IN SELECT * FROM (VALUES
        ('ai_cards_month'), ('ai_cards_dog_name'), ('ai_cards_status'), ('ai_cards_ready_set'),
        ('ai_cards_failed_code'), ('ai_cards_size'), ('ai_cards_likeness'), ('ai_cards_attempts')
    ) AS expected(conname) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint c
            WHERE c.conrelid = relation AND c.conname = item.conname
              AND c.contype = 'c' AND c.convalidated
        ) THEN
            RAISE EXCEPTION 'constraint mismatch: % missing or not validated', item.conname;
        END IF;
    END LOOP;

    -- 인덱스 셋. 부분 UNIQUE 둘은 `WHERE` 가 빠지면 조용히 틀린다 —
    -- one_generating 은 카드를 평생 한 장만 만들게 되고, storage_key 는 생성 중인 카드가 둘일 수 없게 된다.
    -- ⚠️ one_generating 의 predicate 는 'generating' 만 쓰면 안 된다 — 그 낱말이 인덱스
    --    **이름**(idx_ai_cards_one_generating) 에도 들어 있어서, WHERE 가 통째로 빠진
    --    정의에서도 position() 이 이름 쪽에서 매치해 거짓으로 통과한다. PG17 이 WHERE 를
    --    `((status)::text = 'generating'::text)` 로 렌더링하는 것을 이용해 `'::text` 까지
    --    포함시켜 이름 문자열과 겹치지 않게 한다.
    FOR item IN SELECT * FROM (VALUES
        ('idx_ai_cards_owner_created', 'false', NULL),
        ('idx_ai_cards_storage_key', 'true', 'storage_key IS NOT NULL'),
        ('idx_ai_cards_one_generating', 'true', '''generating''::text')
    ) AS expected(index_name, unique_wanted, predicate) LOOP
        definition := NULL;
        SELECT pg_get_indexdef(i.indexrelid) INTO definition
        FROM pg_index i
        JOIN pg_class c ON c.oid = i.indexrelid
        WHERE i.indrelid = relation AND c.relname = item.index_name
          AND i.indisvalid AND i.indisready
          AND i.indisunique = item.unique_wanted::boolean;
        IF definition IS NULL THEN
            RAISE EXCEPTION 'index mismatch: % missing, invalid, or unique flag is not %',
                item.index_name, item.unique_wanted;
        END IF;
        IF item.predicate IS NOT NULL AND position(item.predicate IN definition) = 0 THEN
            RAISE EXCEPTION 'index mismatch: % lost its WHERE %, got %',
                item.index_name, item.predicate, definition;
        END IF;
    END LOOP;

    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
        WHERE t.tgrelid = relation AND t.tgname = 'trg_ai_cards_updated_at'
          AND NOT t.tgisinternal
    ) THEN
        RAISE EXCEPTION 'trigger mismatch: trg_ai_cards_updated_at missing';
    END IF;
END
$verify$;

-- 사람이 눈으로 보는 자리. 적용 직후에는 전부 0 이다.
SELECT status, count(*) FROM ai_cards GROUP BY status ORDER BY status;
