-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — `tools/check_migration_verification.py` 가 스키마를 일부러 망가뜨린 뒤
-- 이 파일이 그것을 잡는지 본다 (CHECKS 의 `vet_visits` 항목). SELECT 만 있으면 틀려도
-- 통과하므로 사람이 볼 질의는 단언 뒤에 둔다.
--
-- ⚠ 실패 문구에 `mismatch` 나 `missing table` 이 들어가야 한다 — 하네스가 그 낱말로
--   "verifier 가 잡았다" 와 "변조 SQL 이 죽었다" 를 가른다.
--
-- 제일 중요한 단언은 **reason_code 의 닫힌 목록**과 **reason_code NOT NULL** 이다.
-- 전자가 풀리면 사유가 자유 텍스트가 되어 사유별 누계가 조용히 쪼개지고, 후자가 풀리면
-- 유저가 확정 안 한 행이 vet_visits 에 앉을 수 있다 — 그 순간 기계가 지어낸 병명이
-- 저장소 목록과 프롬프트에 의료 기록처럼 실린다. 이 표를 둘로 가른 이유가 거기 있다.
--
-- 초안 쪽에서 제일 중요한 것은 **멱등키 UNIQUE (app_user_id, client_event_id)** 다. 그것이
-- 빠지면 얼어 보이는 화면에서 두 번 눌린 업로드가 초안 두 줄과 사진 두 장을 만들고
-- **Gemini 를 두 번 부른다** — 아무 에러도 안 나고 요금만 는다.
DO $verify$
DECLARE
    item record;
    relation regclass;
    drafts regclass;
    missing text;
BEGIN
    IF to_regclass('vet_visits') IS NULL THEN
        RAISE EXCEPTION 'missing table: vet_visits';
    END IF;
    IF to_regclass('vet_visit_drafts') IS NULL THEN
        RAISE EXCEPTION 'missing table: vet_visit_drafts';
    END IF;
    relation := to_regclass('vet_visits');
    drafts := to_regclass('vet_visit_drafts');

    -- ① vet_visits 의 열과 타입. NOT NULL 까지 본다.
    FOR item IN SELECT * FROM (VALUES
        ('id',                    'uuid',                     'true'),
        ('app_user_id',           'uuid',                     'true'),
        ('pet_id',                'uuid',                     'true'),
        ('visited_on',            'date',                     'true'),
        ('total_krw',             'integer',                  'true'),
        ('hospital_name',         'character varying(60)',    'false'),
        ('hospital_address',      'character varying(200)',   'false'),
        ('hospital_phone',        'character varying(32)',    'false'),
        ('reason_code',           'character varying(20)',    'true'),
        ('reason_detail',         'character varying(60)',    'false'),
        ('suggested_reason_code', 'character varying(20)',    'false'),
        ('is_emergency',          'boolean',                  'true'),
        ('is_oncology',           'boolean',                  'true'),
        ('raw_ocr_items',         'jsonb',                    'true'),
        ('receipt_image_key',     'text',                     'false'),
        ('client_event_id',       'uuid',                     'true'),
        ('created_at',            'timestamp with time zone', 'true')
    ) AS expected(column_name, type_name, required) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = relation AND a.attname = item.column_name
              AND a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) = item.type_name
              AND a.attnotnull = item.required::boolean
        ) THEN
            RAISE EXCEPTION 'column mismatch: vet_visits.% (type %, not null %)',
                item.column_name, item.type_name, item.required;
        END IF;
    END LOOP;

    -- ② vet_visit_drafts 의 열과 타입.
    FOR item IN SELECT * FROM (VALUES
        ('id',                'uuid',                     'true'),
        ('app_user_id',       'uuid',                     'true'),
        ('pet_id',            'uuid',                     'true'),
        ('receipt_image_key', 'text',                     'true'),
        ('extracted',         'jsonb',                    'false'),
        ('extracted_at',      'timestamp with time zone', 'false'),
        ('client_event_id',   'uuid',                     'true'),
        ('receipt_sha256',    'character(64)',            'false'),
        ('created_at',        'timestamp with time zone', 'true')
    ) AS expected(column_name, type_name, required) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = drafts AND a.attname = item.column_name
              AND a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) = item.type_name
              AND a.attnotnull = item.required::boolean
        ) THEN
            RAISE EXCEPTION 'column mismatch: vet_visit_drafts.% (type %, not null %)',
                item.column_name, item.type_name, item.required;
        END IF;
    END LOOP;

    -- ③ PK · FK 넷(전부 CASCADE) · 멱등키 UNIQUE. 열 순서까지 본다.
    --    멱등키가 (app_user_id, client_event_id) 인 것이 care_events 와 다르다 —
    --    재시도 도중 활성 강아지가 바뀌어도 두 줄이 안 되게 유저 단위로 묶는다.
    FOR item IN SELECT * FROM (VALUES
        ('vet_visits',       'p', 'id',                          NULL,        NULL, NULL),
        ('vet_visits',       'f', 'app_user_id',                 'app_users', 'id', 'c'),
        ('vet_visits',       'f', 'pet_id',                      'pets',      'id', 'c'),
        ('vet_visits',       'u', 'app_user_id,client_event_id', NULL,        NULL, NULL),
        ('vet_visit_drafts', 'p', 'id',                          NULL,        NULL, NULL),
        ('vet_visit_drafts', 'f', 'app_user_id',                 'app_users', 'id', 'c'),
        ('vet_visit_drafts', 'f', 'pet_id',                      'pets',      'id', 'c'),
        ('vet_visit_drafts', 'u', 'app_user_id,client_event_id', NULL,        NULL, NULL)
    ) AS expected(table_name, kind, columns, target_table, target_columns, delete_action) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint c
            WHERE c.conrelid = to_regclass(item.table_name) AND c.contype::text = item.kind
              AND c.convalidated
              AND ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(num, pos)
                        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.num
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
            RAISE EXCEPTION 'constraint mismatch: % kind % columns %',
                item.table_name, item.kind, item.columns;
        END IF;
    END LOOP;

    -- ④ CHECK 열하나. 이름으로 본다 — 빠져도 칸 모양은 그대로라 ①② 는 통과한다.
    SELECT string_agg(want.name, ', ') INTO missing
    FROM (VALUES
        ('vet_visits_total_krw_range'),
        ('vet_visits_hospital_name_not_blank'),
        ('vet_visits_hospital_address_not_blank'),
        ('vet_visits_hospital_phone_shape'),
        ('vet_visits_reason_code_check'),
        ('vet_visits_reason_detail_not_blank'),
        ('vet_visits_suggested_reason_code_check'),
        ('vet_visits_raw_ocr_items_is_array'),
        ('vet_visits_receipt_image_key_not_blank')
    ) AS want(name)
    WHERE NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        WHERE c.conrelid = relation AND c.conname = want.name AND c.contype = 'c'
    );
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'constraint mismatch on vet_visits: %', missing;
    END IF;

    SELECT string_agg(want.name, ', ') INTO missing
    FROM (VALUES
        ('vet_visit_drafts_receipt_image_key_not_blank'),
        ('vet_visit_drafts_extracted_is_object'),
        ('vet_visit_drafts_receipt_sha256_shape')
    ) AS want(name)
    WHERE NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        WHERE c.conrelid = drafts AND c.conname = want.name AND c.contype = 'c'
    );
    IF missing IS NOT NULL THEN
        RAISE EXCEPTION 'constraint mismatch on vet_visit_drafts: %', missing;
    END IF;

    -- ⑤ 조회 인덱스 둘. 초안 쪽은 24시간 청소가 이것으로 간다.
    FOR item IN SELECT * FROM (VALUES
        ('vet_visits',       'idx_vet_visits_pet_visited'),
        ('vet_visit_drafts', 'idx_vet_visit_drafts_created_at'),
        ('vet_visit_drafts', 'idx_vet_visit_drafts_user_sha')
    ) AS expected(table_name, index_name) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_class i
            JOIN pg_index x ON x.indexrelid = i.oid
            WHERE x.indrelid = to_regclass(item.table_name) AND i.relname = item.index_name
        ) THEN
            RAISE EXCEPTION 'index mismatch on %: %', item.table_name, item.index_name;
        END IF;
    END LOOP;

    -- ⑥ 사유가 **닫힌 목록**이어야 한다. CHECK 이름만 보면 목록을 통째로 갈아 끼운
    --    변조를 못 잡는다 — 실제 값을 정의문에서 확인한다. 이것이 풀리면 피부염·피부질환·
    --    피부병이 서로 다른 키가 되어 사유별 누계가 조용히 쪼개진다.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        WHERE c.conrelid = relation AND c.conname = 'vet_visits_reason_code_check'
          AND pg_get_constraintdef(c.oid) LIKE '%''cardiac''%'
          AND pg_get_constraintdef(c.oid) LIKE '%''other''%'
    ) THEN
        RAISE EXCEPTION 'constraint mismatch on vet_visits: reason_code 가 닫힌 목록이 아니다';
    END IF;

    -- ⑦ 목록의 **축이 하나여야 한다.** care_events 의 kind 에 'walk' 가 없어야 하는 것과
    --    같은 종류의 단언이다 — 병리(tumor·injury·parasite)와 응급도(emergency)는 방문이
    --    겨눈 대상이 아니라 방문의 성질이라, 코드로 되돌아오면 한 방문에 코드가 둘씩
    --    맞아떨어진다. 피부 종괴가 skin 이자 tumor 가 되는 순간, 지키려던 누계가 바로
    --    그 지점에서 깨진다. 응급도는 is_emergency, 종양은 is_oncology 가 받는다.
    FOR item IN SELECT * FROM (VALUES
        ('tumor'), ('injury'), ('parasite'), ('emergency')
    ) AS banned(code) LOOP
        IF EXISTS (
            SELECT 1 FROM pg_constraint c
            WHERE c.conrelid = relation AND c.conname = 'vet_visits_reason_code_check'
              AND pg_get_constraintdef(c.oid) LIKE '%''' || item.code || '''%'
        ) THEN
            RAISE EXCEPTION
                'constraint mismatch on vet_visits: reason_code 에 % 가 있으면 안 된다 (축이 둘이 된다)',
                item.code;
        END IF;
    END LOOP;

    -- ⑧ 불리언 둘의 기본값. false 가 아니면 아무 에러 없이 **모든 방문이 응급·종양으로**
    --    쌓이고, 화면에서만 이상해 보인다 (pets_registered 의 기본값 변조와 같은 함정).
    FOR item IN SELECT * FROM (VALUES
        ('is_emergency'), ('is_oncology')
    ) AS expected(column_name) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attrdef d
            JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
            WHERE d.adrelid = relation AND a.attname = item.column_name
              AND pg_get_expr(d.adbin, d.adrelid) = 'false'
        ) THEN
            RAISE EXCEPTION 'column mismatch: vet_visits.% 의 기본값이 false 가 아니다',
                item.column_name;
        END IF;
    END LOOP;
END
$verify$;

-- 사람이 눈으로 볼 것 (단언이 다 통과한 뒤에만 여기 온다).
SELECT c.relname AS table_name, a.attname AS column_name,
       format_type(a.atttypid, a.atttypmod) AS type, a.attnotnull
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
WHERE a.attrelid IN (to_regclass('vet_visits'), to_regclass('vet_visit_drafts'))
  AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY c.relname, a.attnum;
