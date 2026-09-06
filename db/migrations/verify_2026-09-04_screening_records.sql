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
    IF to_regclass('screening_records') IS NULL THEN
        RAISE EXCEPTION 'missing table: screening_records';
    END IF;
    relation := to_regclass('screening_records');

    FOR item IN SELECT * FROM (VALUES
        ('id', 'uuid', 'true'),
        ('app_user_id', 'uuid', 'true'),
        -- 강아지를 지워도 기록은 남는다 (아래 FK 의 SET NULL 과 짝이다)
        ('pet_id', 'uuid', 'false'),
        ('status', 'character varying(20)', 'true'),
        -- **사진 두 칸은 NOT NULL 이다.** 사진 자체가 기록이라 없는 기록은 성립하지 않는다 (D-052)
        ('photo_storage_key', 'character varying(200)', 'true'),
        ('photo_content_type', 'character varying(40)', 'true'),
        ('photo_generation', 'character varying(64)', 'false'),
        ('photo_size_bytes', 'integer', 'false'),
        ('box', 'jsonb', 'false'),
        -- 판정 원본. **열로 펼치지 않는다** — 옛 계약으로 판정된 기록도 그대로 남아야 한다
        ('result', 'jsonb', 'false'),
        ('contract_version', 'character varying(20)', 'false'),
        ('failure_reason', 'text', 'false'),
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
            RAISE EXCEPTION 'column mismatch: screening_records.% (type %, not null %)',
                item.column_name, item.type_name, item.required;
        END IF;
    END LOOP;

    -- FK 둘의 **삭제 동작이 서로 다르다** — dog_cards 와 같은 이유다.
    -- 계정은 CASCADE(탈퇴하면 개인정보 파기), 강아지는 SET NULL(기록은 남는다).
    FOR item IN SELECT * FROM (VALUES
        ('p', 'id', NULL, NULL, NULL),
        ('f', 'app_user_id', 'app_users', 'id', 'c'),
        ('f', 'pet_id', 'pets', 'id', 'n')
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
            RAISE EXCEPTION 'constraint mismatch: screening_records kind % columns %',
                item.kind, item.columns;
        END IF;
    END LOOP;

    -- CHECK 셋은 이름이 자동 생성이라(`{table}_{column}_check`) 이름 대신 **개수와 대상 칸**으로 센다.
    -- status · photo_content_type · photo_size_bytes 에 각각 하나씩 있어야 한다.
    FOR item IN SELECT * FROM (VALUES
        ('status'), ('photo_content_type'), ('photo_size_bytes')
    ) AS expected(column_name) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint c
            JOIN pg_attribute a ON a.attrelid = relation AND a.attnum = ANY(c.conkey)
            WHERE c.conrelid = relation AND c.contype = 'c' AND c.convalidated
              AND a.attname = item.column_name
        ) THEN
            RAISE EXCEPTION 'constraint mismatch: no CHECK on screening_records.%',
                item.column_name;
        END IF;
    END LOOP;

    -- 인덱스 셋. `photo_key` 는 UNIQUE 다 — 같은 사진이 두 기록이 되면 판정이 중복된다.
    FOR item IN SELECT * FROM (VALUES
        ('idx_screening_records_pet_created', 'false'),
        ('idx_screening_records_owner_created', 'false'),
        ('idx_screening_records_photo_key', 'true')
    ) AS expected(index_name, unique_wanted) LOOP
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
    END LOOP;

    -- `updated_at` 트리거. 없으면 그 칸이 만든 시각에서 영영 멈춘다 — 조용히 틀린다.
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
        WHERE t.tgrelid = relation AND t.tgname = 'trg_screening_records_updated_at'
          AND NOT t.tgisinternal
    ) THEN
        RAISE EXCEPTION 'trigger mismatch: trg_screening_records_updated_at missing';
    END IF;
END
$verify$;

-- ─────────────────────────────────────────────────────────────────────────
-- 사람이 눈으로 보는 자리. 위 단언이 통과한 뒤에만 여기까지 온다.
-- ─────────────────────────────────────────────────────────────────────────

-- 상태별 기록 수. 적용 직후에는 전부 0이다.
SELECT status, count(*) AS n FROM screening_records GROUP BY status ORDER BY n DESC;

-- 판정이 끝났는데 결과가 비어 있는 기록. **0행이어야 한다.**
SELECT count(*) AS done_without_result
FROM screening_records WHERE status = 'DONE' AND result IS NULL;
