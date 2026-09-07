-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — 이유는 verify_2026-09-05_app_user_nickname.sql 머리말과 같다.
-- 사람이 눈으로 볼 질의는 단언 뒤에 남겼다.
-- (2026-09-07 #295 에 단언형으로 다시 썼다. 그 전에는 SELECT 나열이라 **틀려도 녹색**이었다.)
--
-- 표 둘 다 `walk_analyses` 에 매달린다 — 계산 결과 하나에서 파생되는 것들이라, **계산이
-- 사라지면 같이 사라져야 한다**(둘 다 CASCADE). 그리고 둘 다 **봉인된 값**이라 나중에 고치지
-- 않는다: Capsule 은 `sealed_at`, Cellophane 은 `sheet_fingerprint` 가 그 뜻을 진다.
DO $verify$
DECLARE
    item record;
    capsules regclass;
    sheets regclass;
    definition text;
BEGIN
    IF to_regclass('walk_capsules') IS NULL THEN
        RAISE EXCEPTION 'missing table: walk_capsules';
    END IF;
    IF to_regclass('walk_cellophane_sheets') IS NULL THEN
        RAISE EXCEPTION 'missing table: walk_cellophane_sheets';
    END IF;
    capsules := to_regclass('walk_capsules');
    sheets := to_regclass('walk_cellophane_sheets');

    FOR item IN SELECT * FROM (VALUES
        ('walk_capsules', 'analysis_id', 'uuid', 'true'),
        ('walk_capsules', 'capsule_version', 'integer', 'true'),
        ('walk_capsules', 'context_version', 'integer', 'true'),
        ('walk_capsules', 'capabilities', 'jsonb', 'true'),
        ('walk_capsules', 'trail_context', 'jsonb', 'true'),
        -- 봉인한 시각. `derived_at` 처럼 기본값을 주지 않는다 — **부르는 쪽이 정한다.**
        ('walk_capsules', 'sealed_at', 'timestamp with time zone', 'true'),
        ('walk_cellophane_sheets', 'analysis_id', 'uuid', 'true'),
        -- 무엇을 칠했나. PK 의 절반이라 **같은 계산에 여러 장**이 공존한다.
        ('walk_cellophane_sheets', 'paint_fp', 'character varying(128)', 'true'),
        ('walk_cellophane_sheets', 'sheet_schema_version', 'integer', 'true'),
        ('walk_cellophane_sheets', 'paint_version', 'integer', 'true'),
        ('walk_cellophane_sheets', 'grid_version', 'character varying(64)', 'true'),
        ('walk_cellophane_sheets', 'radius_u', 'double precision', 'true'),
        ('walk_cellophane_sheets', 'profile', 'character varying(128)', 'true'),
        ('walk_cellophane_sheets', 'profile_fp', 'character varying(128)', 'true'),
        ('walk_cellophane_sheets', 'sample_step_m', 'double precision', 'true'),
        ('walk_cellophane_sheets', 'cell_count', 'integer', 'true'),
        ('walk_cellophane_sheets', 'sheet_fingerprint', 'character varying(71)', 'true'),
        ('walk_cellophane_sheets', 'payload', 'jsonb', 'true'),
        ('walk_cellophane_sheets', 'derived_at', 'timestamp with time zone', 'true')
    ) AS expected(table_name, column_name, type_name, required) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = to_regclass(item.table_name) AND a.attname = item.column_name
              AND a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) = item.type_name
              AND a.attnotnull = item.required::boolean
        ) THEN
            RAISE EXCEPTION 'column mismatch: %.% (type %, not null %)',
                item.table_name, item.column_name, item.type_name, item.required;
        END IF;
    END LOOP;

    -- PK 가 서로 다르다.
    --   `walk_capsules`         — `analysis_id` **하나**. 계산 하나에 캡슐 하나다
    --   `walk_cellophane_sheets` — `(analysis_id, paint_fp)`. 같은 계산에 **여러 장**이다
    -- 뒤쪽 PK 를 `analysis_id` 하나로 줄이면 다른 물감으로 칠한 장이 서로를 덮어쓴다.
    FOR item IN SELECT * FROM (VALUES
        ('walk_capsules', 'p', 'analysis_id', NULL, NULL),
        ('walk_capsules', 'f', 'analysis_id', 'walk_analyses', 'c'),
        ('walk_cellophane_sheets', 'p', 'analysis_id,paint_fp', NULL, NULL),
        ('walk_cellophane_sheets', 'f', 'analysis_id', 'walk_analyses', 'c')
    ) AS expected(table_name, kind, columns, target_table, delete_action) LOOP
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
              ))
        ) THEN
            RAISE EXCEPTION 'constraint mismatch: % kind % columns %',
                item.table_name, item.kind, item.columns;
        END IF;
    END LOOP;

    -- CHECK. jsonb 의 **모양**까지 묶는 자리가 셋이다 — 타입만으로는 배열이 들어와도 통과한다.
    -- `capabilities` 는 **빈 배열도 막는다**: 아무것도 못 하는 캡슐은 캡슐이 아니다.
    FOR item IN SELECT * FROM (VALUES
        ('walk_capsules', 'walk_capsules_capabilities_array', 'array'),
        ('walk_capsules', 'walk_capsules_context_object', 'object'),
        ('walk_capsules', 'walk_capsules_versions_positive', NULL),
        ('walk_cellophane_sheets', 'walk_cellophane_payload_object', 'object'),
        ('walk_cellophane_sheets', 'walk_cellophane_fingerprint_check', 'sha256'),
        ('walk_cellophane_sheets', 'walk_cellophane_versions_positive', NULL),
        ('walk_cellophane_sheets', 'walk_cellophane_spec_positive', NULL),
        ('walk_cellophane_sheets', 'walk_cellophane_cell_count_check', NULL),
        -- 신원 네 칸이 **빈 문자열이면 안 된다.** NOT NULL 은 빈 문자열을 안 막는데,
        -- `paint_fp` 가 빈 값이면 PK 절반이 무의미해진다.
        ('walk_cellophane_sheets', 'walk_cellophane_identity_nonempty', NULL)
    ) AS expected(table_name, conname, fragment) LOOP
        SELECT pg_get_constraintdef(c.oid) INTO definition
        FROM pg_constraint c
        WHERE c.conrelid = to_regclass(item.table_name) AND c.conname = item.conname
          AND c.contype = 'c' AND c.convalidated;
        IF definition IS NULL THEN
            RAISE EXCEPTION 'constraint mismatch: % missing or not validated', item.conname;
        END IF;
        IF item.fragment IS NOT NULL AND position(item.fragment IN definition) = 0 THEN
            RAISE EXCEPTION 'constraint mismatch: % lost %, got %',
                item.conname, item.fragment, definition;
        END IF;
    END LOOP;

    -- `capabilities` 의 **빈 배열 금지**를 한 번 더 못 박는다. 위 검사는 `array` 라는 낱말만
    -- 보는데, 길이 조건이 빠져도 그 낱말은 남는다.
    SELECT pg_get_constraintdef(c.oid) INTO definition
    FROM pg_constraint c
    WHERE c.conrelid = capsules AND c.conname = 'walk_capsules_capabilities_array';
    IF position('jsonb_array_length' IN definition) = 0 THEN
        RAISE EXCEPTION 'constraint mismatch: capabilities must also be non-empty, got %',
            definition;
    END IF;

    -- 같은 물감으로 칠한 장을 가로질러 찾는 질의. PK 가 `analysis_id` 로 시작해 못 탄다.
    IF NOT EXISTS (
        SELECT 1 FROM pg_index i
        JOIN pg_class c ON c.oid = i.indexrelid
        WHERE i.indrelid = sheets AND c.relname = 'walk_cellophane_paint_fp_idx'
          AND i.indisvalid AND i.indisready
    ) THEN
        RAISE EXCEPTION 'index mismatch: walk_cellophane_paint_fp_idx missing or invalid';
    END IF;
END
$verify$;

-- ─────────────────────────────────────────────────────────────────────────
-- 사람이 눈으로 보는 자리. 위 단언이 통과한 뒤에만 여기까지 온다.
-- **이 아래는 이 파일이 원래 갖고 있던 질의 그대로다** — 단언을 더하면서
-- 갈아치우지 않는다. 카탈로그로는 못 보는 것을 보기 때문이다
-- (jsonb 키 집합 · 표 사이의 신원 일치 · 사진 삭제 대기 …).
-- ─────────────────────────────────────────────────────────────────────────

SELECT count(*) AS derived_without_capsule
FROM walk_analyses AS analysis
LEFT JOIN walk_capsules AS capsule ON capsule.analysis_id = analysis.id
WHERE capsule.analysis_id IS NULL;

SELECT count(*) AS invalid_capsule_shape
FROM walk_capsules
WHERE capsule_version <= 0
   OR context_version <= 0
   OR jsonb_typeof(capabilities) <> 'array'
   OR jsonb_array_length(capabilities) = 0
   OR jsonb_typeof(trail_context) <> 'object'
   OR NOT trail_context ?& ARRAY[
       'context_version',
       'walk_id',
       'status',
       'walked_at',
       'source_observed_at',
       'captured_at',
       'provider',
       'weather_code',
       'is_day',
       'temperature_c',
       'precipitation_mm',
       'humidity_pct',
       'sun_elevation_deg',
       'failure_reason'
   ]
   OR jsonb_typeof(trail_context -> 'context_version') IS DISTINCT FROM 'number'
   OR jsonb_typeof(trail_context -> 'walk_id') IS DISTINCT FROM 'string'
   OR jsonb_typeof(trail_context -> 'status') IS DISTINCT FROM 'string'
   OR jsonb_typeof(trail_context -> 'walked_at') IS DISTINCT FROM 'string'
   OR jsonb_typeof(trail_context -> 'captured_at') IS DISTINCT FROM 'string';

SELECT count(*) AS mismatched_context_identity
FROM walk_capsules AS capsule
JOIN walk_analyses AS analysis ON analysis.id = capsule.analysis_id
WHERE capsule.context_version IS DISTINCT FROM CASE
        WHEN pg_input_is_valid(
            capsule.trail_context ->> 'context_version',
            'integer'
        )
        THEN (capsule.trail_context ->> 'context_version')::integer
        ELSE NULL
    END
   OR analysis.walk_id::text IS DISTINCT FROM capsule.trail_context ->> 'walk_id'
   OR CASE
        WHEN pg_input_is_valid(
            capsule.trail_context ->> 'walked_at',
            'timestamp with time zone'
        )
        THEN FALSE
        ELSE TRUE
    END
   OR CASE
        WHEN pg_input_is_valid(
            capsule.trail_context ->> 'captured_at',
            'timestamp with time zone'
        )
        THEN capsule.sealed_at <
            (capsule.trail_context ->> 'captured_at')::timestamptz
        ELSE TRUE
    END;

SELECT
    analysis.walk_id,
    capsule.analysis_id,
    capsule.capsule_version,
    capsule.trail_context ->> 'status' AS context_status,
    capsule.trail_context ->> 'provider' AS context_provider,
    capsule.sealed_at
FROM walk_capsules AS capsule
JOIN walk_analyses AS analysis ON analysis.id = capsule.analysis_id
ORDER BY capsule.sealed_at DESC
LIMIT 10;
