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
    IF to_regclass('admin_ai_cards') IS NULL THEN
        RAISE EXCEPTION 'missing table: admin_ai_cards';
    END IF;
    relation := to_regclass('admin_ai_cards');

    -- 이 표에는 status 가 없다 — 콘솔 생성이 동기라 다 만들어진 카드만 들어온다.
    -- 그래서 이미지 칸(storage_key·size_bytes·width·height)이 **전부 NOT NULL** 이다.
    -- 하나라도 널 허용으로 풀리면 빈 카드 행이 목록에 설 수 있다.
    FOR item IN SELECT * FROM (VALUES
        ('id', 'uuid', 'true'),
        ('admin_user_id', 'uuid', 'true'),
        -- 달이 아닌 카드가 같은 칸에 들어오므로 정수가 아니다
        ('card_key', 'character varying(20)', 'true'),
        ('dog_name', 'character varying(40)', 'true'),
        ('title', 'character varying(80)', 'true'),
        ('engine', 'character varying(20)', 'true'),
        -- smallint 로 좁아지면 32767 을 넘는 seed 가 조용히 잘린다
        ('seed', 'integer', 'false'),
        ('attempts', 'smallint', 'true'),
        ('likeness', 'smallint', 'false'),
        ('judge_note', 'character varying(200)', 'false'),
        ('storage_key', 'character varying(200)', 'true'),
        ('size_bytes', 'integer', 'true'),
        ('width', 'integer', 'true'),
        ('height', 'integer', 'true'),
        ('elapsed_ms', 'integer', 'true'),
        ('created_at', 'timestamp with time zone', 'true')
    ) AS expected(column_name, type_name, required) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = relation AND a.attname = item.column_name
              AND a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) = item.type_name
              AND a.attnotnull = item.required::boolean
        ) THEN
            RAISE EXCEPTION 'column mismatch: admin_ai_cards.% (type %, not null %)',
                item.column_name, item.type_name, item.required;
        END IF;
    END LOOP;

    -- FK 하나뿐이고 CASCADE 다. 관리자 계정은 status 로 막지 지우지 않으므로 거의 안 돈다.
    FOR item IN SELECT * FROM (VALUES
        ('p', 'id', NULL, NULL, NULL),
        ('f', 'admin_user_id', 'admin_users', 'id', 'c')
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
            RAISE EXCEPTION 'constraint mismatch: admin_ai_cards kind % columns %',
                item.kind, item.columns;
        END IF;
    END LOOP;

    FOR item IN SELECT * FROM (VALUES
        ('admin_ai_cards_engine'), ('admin_ai_cards_card_key'),
        ('admin_ai_cards_likeness'), ('admin_ai_cards_size')
    ) AS expected(conname) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint c
            WHERE c.conrelid = relation AND c.conname = item.conname
              AND c.contype = 'c' AND c.convalidated
        ) THEN
            RAISE EXCEPTION 'constraint mismatch: % missing or not validated', item.conname;
        END IF;
    END LOOP;

    -- 인덱스 둘. 세 번째 칸은 정의 안에 **반드시 있어야 하는 조각**이다.
    -- ⚠️ created 쪽은 `DESC` 가 빠져도 인덱스는 멀쩡히 서고 에러도 안 난다 — 목록이 조용히
    --    오래된 것부터 나올 뿐이라, 이름만 보는 검사로는 아무 의미가 없다.
    --    조각을 `created_at DESC` 로 둔 것은 인덱스 **이름**(idx_admin_ai_cards_created)에
    --    그 문자열이 안 들어 있어 이름 쪽에서 거짓으로 매치될 일이 없기 때문이다.
    FOR item IN SELECT * FROM (VALUES
        ('idx_admin_ai_cards_created', 'false', 'created_at DESC'),
        ('idx_admin_ai_cards_storage_key', 'true', NULL)
    ) AS expected(index_name, unique_wanted, fragment) LOOP
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
        IF item.fragment IS NOT NULL AND position(item.fragment IN definition) = 0 THEN
            RAISE EXCEPTION 'index mismatch: % lost its %, got %',
                item.index_name, item.fragment, definition;
        END IF;
    END LOOP;
END
$verify$;

-- 사람이 눈으로 보는 자리. 적용 직후에는 아무것도 안 나온다.
SELECT engine, count(*) FROM admin_ai_cards GROUP BY engine ORDER BY engine;
