-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — 이유는 verify_2026-09-05_app_user_nickname.sql 머리말과 같다.
-- 사람이 눈으로 볼 질의는 단언 뒤에 남겼다.
DO $verify$
DECLARE
    item record;
    relation regclass;
    definition text;
BEGIN
    IF to_regclass('dog_cards') IS NULL THEN
        RAISE EXCEPTION 'missing table: dog_cards';
    END IF;
    relation := to_regclass('dog_cards');

    FOR item IN SELECT * FROM (VALUES
        -- **`id` 에 DEFAULT 가 없다.** 앱이 만든다 — 오프라인에서 먼저 만들어지기 때문이다(D-052).
        ('id', 'uuid', 'true'),
        ('app_user_id', 'uuid', 'true'),
        ('template_id', 'character varying(80)', 'true'),
        -- 강아지를 지워도 카드는 남는다. 그래서 NULL 을 허용한다 (아래 FK 의 SET NULL 과 짝이다)
        ('dog_id', 'uuid', 'false'),
        ('dog_name', 'character varying(40)', 'true'),
        ('drawn_at', 'timestamp with time zone', 'true'),
        ('code_text', 'character varying(40)', 'true'),
        ('user_framed', 'boolean', 'true'),
        ('core_left', 'integer', 'true'),
        ('core_top', 'integer', 'true'),
        ('core_right', 'integer', 'true'),
        ('core_bottom', 'integer', 'true'),
        ('face_storage_key', 'character varying(200)', 'false'),
        ('face_generation', 'character varying(64)', 'false'),
        ('face_size_bytes', 'integer', 'false'),
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
            RAISE EXCEPTION 'column mismatch: dog_cards.% (type %, not null %)',
                item.column_name, item.type_name, item.required;
        END IF;
    END LOOP;

    -- FK 둘의 **삭제 동작이 서로 다르다**. 계정은 CASCADE(탈퇴하면 카드도 파기),
    -- 강아지는 SET NULL(무지개다리를 건너도 뽑아 둔 카드는 남는다). 뒤바뀌면 조용히 데이터를 잃는다.
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
              AND (item.kind NOT IN ('p', 'u') OR EXISTS (
                  SELECT 1 FROM pg_index i WHERE i.indexrelid = c.conindid
                    AND i.indisvalid AND i.indisready AND i.indisunique
              ))
        ) THEN
            RAISE EXCEPTION 'constraint mismatch: dog_cards kind % columns %',
                item.kind, item.columns;
        END IF;
    END LOOP;

    -- CHECK 둘. `core_rect` 는 좌표가 뒤집힌 카드를 막고, `face_set` 은 얼굴 PNG 의
    -- 세 칸이 **함께** 채워지는지를 지킨다.
    FOR item IN SELECT * FROM (VALUES
        ('dog_cards_core_rect'), ('dog_cards_face_set')
    ) AS expected(conname) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint c
            WHERE c.conrelid = relation AND c.conname = item.conname
              AND c.contype = 'c' AND c.convalidated
        ) THEN
            RAISE EXCEPTION 'constraint mismatch: % missing or not validated', item.conname;
        END IF;
    END LOOP;

    -- 인덱스 셋. `face_key` 는 **부분** UNIQUE 다 — `WHERE` 가 빠지면 얼굴 없는 카드가
    -- 둘 이상일 수 없게 된다.
    FOR item IN SELECT * FROM (VALUES
        ('idx_dog_cards_owner_drawn', 'false', NULL),
        ('idx_dog_cards_owner_template', 'false', NULL),
        ('idx_dog_cards_face_key', 'true', 'face_storage_key IS NOT NULL')
    ) AS expected(index_name, unique_wanted, predicate) LOOP
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

    -- `updated_at` 트리거. 없으면 그 칸이 만든 시각에서 영영 멈춘다 — 조용히 틀린다.
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
        WHERE t.tgrelid = relation AND t.tgname = 'trg_dog_cards_updated_at'
          AND NOT t.tgisinternal
    ) THEN
        RAISE EXCEPTION 'trigger mismatch: trg_dog_cards_updated_at missing';
    END IF;
END
$verify$;

-- ─────────────────────────────────────────────────────────────────────────
-- 사람이 눈으로 보는 자리. 위 단언이 통과한 뒤에만 여기까지 온다.
-- ─────────────────────────────────────────────────────────────────────────

-- 카드 수와 얼굴 PNG 를 가진 카드 수. 적용 직후에는 둘 다 0이다.
SELECT count(*) AS cards_total,
       count(face_storage_key) AS with_face,
       count(dog_id) AS linked_to_a_pet
FROM dog_cards;

-- 좌표가 뒤집힌 카드. **0행이어야 한다** (제약이 막지만 눈으로도 본다).
SELECT count(*) AS bad_rect
FROM dog_cards WHERE core_right <= core_left OR core_bottom <= core_top;
