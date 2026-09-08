-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — `tools/check_migration_verification.py` 가 스키마를 일부러 망가뜨린 뒤
-- 이 파일이 그것을 잡는지 본다 (CHECKS 의 `care_events` 항목). SELECT 만 있으면 틀려도
-- 통과하므로 사람이 볼 질의는 단언 뒤에 둔다.
--
-- ⚠ 실패 문구에 `mismatch` 나 `missing table` 이 들어가야 한다 — 하네스가 그 낱말로
--   "verifier 가 잡았다" 와 "변조 SQL 이 죽었다" 를 가른다.
--
-- 제일 중요한 단언은 **멱등키 UNIQUE (pet_id, client_event_id)** 다. 그것이 빠지면 앱의
-- 재시도가 두 줄이 되는데, 아무 에러도 안 나서 화면에서 "밥 2번" 으로만 보인다.
DO $verify$
DECLARE
    item record;
    relation regclass;
    missing text;
BEGIN
    IF to_regclass('care_events') IS NULL THEN
        RAISE EXCEPTION 'missing table: care_events';
    END IF;
    relation := to_regclass('care_events');

    -- ① 열과 타입. NOT NULL 까지 본다 — occurred_at 이 nullable 이 되면 "언제" 가 없는
    --    기록이 생기고 하루 요약이 그것을 못 센다.
    FOR item IN SELECT * FROM (VALUES
        ('id',              'uuid',                     'true'),
        ('app_user_id',     'uuid',                     'true'),
        ('pet_id',          'uuid',                     'true'),
        ('kind',            'character varying(12)',    'true'),
        ('occurred_at',     'timestamp with time zone', 'true'),
        ('note',            'character varying(120)',   'false'),
        ('client_event_id', 'uuid',                     'true'),
        ('created_at',      'timestamp with time zone', 'true')
    ) AS expected(column_name, type_name, required) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = relation AND a.attname = item.column_name
              AND a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) = item.type_name
              AND a.attnotnull = item.required::boolean
        ) THEN
            RAISE EXCEPTION 'column mismatch: care_events.% (type %, not null %)',
                item.column_name, item.type_name, item.required;
        END IF;
    END LOOP;

    -- ② PK · FK 둘(둘 다 CASCADE) · 멱등키 UNIQUE. 열 순서까지 본다.
    FOR item IN SELECT * FROM (VALUES
        ('p', 'id',                     NULL,        NULL, NULL),
        ('f', 'app_user_id',            'app_users', 'id', 'c'),
        ('f', 'pet_id',                 'pets',      'id', 'c'),
        ('u', 'pet_id,client_event_id', NULL,        NULL, NULL)
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
            RAISE EXCEPTION 'constraint mismatch: care_events kind % columns %',
                item.kind, item.columns;
        END IF;
    END LOOP;

    -- ③ CHECK 둘. kind 의 오타('meals')는 아무 에러도 안 내면서 하루 요약에서 빠진다.
    SELECT string_agg(want.name, ', ') INTO missing
    FROM (VALUES
        ('care_events_kind_check'),
        ('care_events_note_not_blank')
    ) AS want(name)
    WHERE NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        WHERE c.conrelid = relation AND c.conname = want.name AND c.contype = 'c'
    );
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'constraint mismatch on care_events: %', missing;
    END IF;

    -- ④ 기간 조회 인덱스.
    IF NOT EXISTS (
        SELECT 1 FROM pg_class i
        JOIN pg_index x ON x.indexrelid = i.oid
        WHERE x.indrelid = relation AND i.relname = 'idx_care_events_pet_occurred'
    ) THEN
        RAISE EXCEPTION 'index mismatch on care_events: idx_care_events_pet_occurred';
    END IF;

    -- ⑤ 'walk' 를 kind 로 넣을 수 없어야 한다 — 산책은 walks 가 진실이다.
    IF EXISTS (
        SELECT 1 FROM pg_constraint c
        WHERE c.conrelid = relation AND c.conname = 'care_events_kind_check'
          AND pg_get_constraintdef(c.oid) ILIKE '%''walk''%'
    ) THEN
        RAISE EXCEPTION 'constraint mismatch on care_events: kind 에 walk 가 있으면 안 된다';
    END IF;
END
$verify$;

-- 사람이 눈으로 볼 것 (단언이 다 통과한 뒤에만 여기 온다).
SELECT a.attname AS column_name, format_type(a.atttypid, a.atttypmod) AS type, a.attnotnull
FROM pg_attribute a
WHERE a.attrelid = to_regclass('care_events') AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY a.attnum;
