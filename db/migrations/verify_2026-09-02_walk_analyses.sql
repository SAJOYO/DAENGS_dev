-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — 이유는 verify_2026-09-05_app_user_nickname.sql 머리말과 같다.
-- 사람이 눈으로 볼 질의는 단언 뒤에 남겼다.
-- (2026-09-07 #295 에 단언형으로 다시 썼다. 그 전에는 SELECT 나열이라 **틀려도 녹색**이었다.)
--
-- 산책 좌표에서 **다시 계산할 수 있는 값**을 굳혀 두는 표다. `walks` 가 거리·시간을 일부러
-- 저장하지 않는 것과 짝인데(계산 규칙을 고치면 저장된 수와 갈라진다), 여기서는 **어떤 규칙으로
-- 언제 계산했는가를 같이 적어** 그 문제를 푼다 — 그래서 버전 넷과 지문이 신원의 일부다.
DO $verify$
DECLARE
    item record;
    relation regclass;
    definition text;
BEGIN
    IF to_regclass('walk_analyses') IS NULL THEN
        RAISE EXCEPTION 'missing table: walk_analyses';
    END IF;
    relation := to_regclass('walk_analyses');

    FOR item IN SELECT * FROM (VALUES
        ('id', 'uuid', 'true'),
        ('walk_id', 'uuid', 'true'),
        -- `sha256:<64자>` 다. 입력이 같으면 같은 값이라 **다시 계산할지**를 이걸로 가른다.
        ('input_fingerprint', 'character varying(71)', 'true'),
        ('point_count', 'integer', 'true'),
        -- 점이 0개면 NULL 이다. 아래 `terminal_sequence_check` 가 그 짝을 묶는다.
        ('terminal_client_seq', 'integer', 'false'),
        -- 버전 넷. **어느 규칙으로 계산했나**이고 UNIQUE 의 일부다.
        ('facts_record_version', 'integer', 'true'),
        ('calculation_version', 'integer', 'true'),
        ('receipt_version', 'integer', 'true'),
        ('observation_version', 'integer', 'true'),
        -- 목록이 읽는 요약 셋. jsonb 를 파고들지 않게 꺼내 둔 값이다.
        ('moving_distance_m', 'integer', 'true'),
        ('moving_s', 'integer', 'true'),
        ('stop_count', 'integer', 'true'),
        ('facts', 'jsonb', 'true'),
        ('measurement_receipt', 'jsonb', 'true'),
        ('motion_events', 'jsonb', 'true'),
        ('micro_observations', 'jsonb', 'true'),
        ('derived_at', 'timestamp with time zone', 'true')
    ) AS expected(column_name, type_name, required) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = relation AND a.attname = item.column_name
              AND a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) = item.type_name
              AND a.attnotnull = item.required::boolean
        ) THEN
            RAISE EXCEPTION 'column mismatch: walk_analyses.% (type %, not null %)',
                item.column_name, item.type_name, item.required;
        END IF;
    END LOOP;

    -- PK · FK · **신원 UNIQUE**.
    --
    -- UNIQUE 가 여섯 칸인 것이 이 표의 설계다 — 같은 산책을 **다른 규칙으로** 다시 계산한
    -- 결과가 공존할 수 있어야 한다. 칸이 줄면 규칙을 고친 순간 옛 결과를 덮어쓰고,
    -- 그러면 "그때는 이렇게 쟀다"를 되살릴 수 없다.
    FOR item IN SELECT * FROM (VALUES
        ('p', 'id', NULL, NULL),
        ('f', 'walk_id', 'walks', 'c'),
        ('u', 'walk_id,input_fingerprint,facts_record_version,calculation_version,'
              'receipt_version,observation_version', NULL, NULL)
    ) AS expected(kind, columns, target_table, delete_action) LOOP
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
              ))
        ) THEN
            RAISE EXCEPTION 'constraint mismatch: walk_analyses kind % columns %',
                item.kind, item.columns;
        END IF;
    END LOOP;

    -- CHECK 아홉. jsonb 네 칸의 **모양**(객체냐 배열이냐)까지 묶는 것이 특징이다 —
    -- 타입은 다 `jsonb` 라서 그것만으로는 배열이 들어와도 통과한다.
    FOR item IN SELECT * FROM (VALUES
        ('walk_analyses_facts_object', 'object'),
        ('walk_analyses_receipt_object', 'object'),
        ('walk_analyses_events_array', 'array'),
        ('walk_analyses_observations_array', 'array'),
        ('walk_analyses_input_fingerprint_check', 'sha256'),
        ('walk_analyses_point_count_check', NULL),
        ('walk_analyses_summary_nonnegative', NULL),
        ('walk_analyses_versions_positive', NULL),
        -- 점이 0개면 끝 번호가 NULL, 있으면 `point_count - 1` 이다.
        -- 이 짝이 풀리면 "점은 있는데 끝 번호가 없는" 행이 들어온다.
        ('walk_analyses_terminal_sequence_check', NULL)
    ) AS expected(conname, fragment) LOOP
        SELECT pg_get_constraintdef(c.oid) INTO definition
        FROM pg_constraint c
        WHERE c.conrelid = relation AND c.conname = item.conname
          AND c.contype = 'c' AND c.convalidated;
        IF definition IS NULL THEN
            RAISE EXCEPTION 'constraint mismatch: % missing or not validated', item.conname;
        END IF;
        IF item.fragment IS NOT NULL AND position(item.fragment IN definition) = 0 THEN
            RAISE EXCEPTION 'constraint mismatch: % lost %, got %',
                item.conname, item.fragment, definition;
        END IF;
    END LOOP;

    -- 목록은 "이 산책의 계산 결과, 최근 순"이다. 방향이 뒤집히면 옛 계산이 먼저 나온다.
    SELECT pg_get_indexdef(i.indexrelid) INTO definition
    FROM pg_index i
    JOIN pg_class c ON c.oid = i.indexrelid
    WHERE i.indrelid = relation AND c.relname = 'walk_analyses_walk_derived_idx'
      AND i.indisvalid AND i.indisready;
    IF definition IS NULL THEN
        RAISE EXCEPTION 'index mismatch: walk_analyses_walk_derived_idx missing or invalid';
    END IF;
    IF position('derived_at DESC' IN definition) = 0 THEN
        RAISE EXCEPTION 'index mismatch: walk_analyses_walk_derived_idx lost derived_at DESC, got %',
            definition;
    END IF;

    -- 이 장은 `walks` 에 칸 하나도 더한다. **`collecting`/`derived` 두 값뿐**이고,
    -- 기본값이 `collecting` 이라야 이미 쌓인 산책이 "아직 계산 안 함"으로 들어온다.
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute a
        WHERE a.attrelid = to_regclass('walks') AND a.attname = 'analysis_state'
          AND a.attnum > 0 AND NOT a.attisdropped
          AND format_type(a.atttypid, a.atttypmod) = 'character varying(16)'
          AND a.attnotnull
    ) THEN
        RAISE EXCEPTION 'column mismatch: walks.analysis_state '
                        '(want character varying(16), not null)';
    END IF;
    SELECT pg_get_expr(d.adbin, d.adrelid) INTO definition
    FROM pg_attrdef d
    JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
    WHERE d.adrelid = to_regclass('walks') AND a.attname = 'analysis_state';
    IF definition IS NULL OR position('collecting' IN definition) = 0 THEN
        RAISE EXCEPTION 'column mismatch: walks.analysis_state must default to collecting, got %',
            COALESCE(definition, '<none>');
    END IF;
    SELECT pg_get_constraintdef(c.oid) INTO definition
    FROM pg_constraint c
    WHERE c.conrelid = to_regclass('walks') AND c.conname = 'walks_analysis_state_check'
      AND c.contype = 'c' AND c.convalidated;
    IF definition IS NULL THEN
        RAISE EXCEPTION 'constraint mismatch: walks_analysis_state_check missing or not validated';
    END IF;
    FOR item IN SELECT * FROM (VALUES ('collecting'), ('derived')) AS expected(value) LOOP
        IF position('''' || item.value || '''' IN definition) = 0 THEN
            RAISE EXCEPTION 'constraint mismatch: walks_analysis_state_check lost %, got %',
                item.value, definition;
        END IF;
    END LOOP;
END
$verify$;

-- ─────────────────────────────────────────────────────────────────────────
-- 사람이 눈으로 보는 자리. 위 단언이 통과한 뒤에만 여기까지 온다.
-- **이 아래는 이 파일이 원래 갖고 있던 질의 그대로다** — 단언을 더하면서
-- 갈아치우지 않는다. 카탈로그로는 못 보는 것을 보기 때문이다
-- (jsonb 키 집합 · 표 사이의 신원 일치 · 사진 삭제 대기 …).
-- ─────────────────────────────────────────────────────────────────────────

SELECT
    EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'walks'
          AND column_name = 'analysis_state'
    ) AS walks_has_analysis_state,
    to_regclass('public.walk_analyses') AS analyses_table,
    to_regclass('public.walk_cellophane_sheets') AS sheets_table;

-- 2) 기존 산책은 모두 collecting으로 안전하게 채워졌나
SELECT analysis_state, COUNT(*)
FROM walks
GROUP BY analysis_state
ORDER BY analysis_state;

-- 3) 분석 identity 중복이 없는가
SELECT
    walk_id,
    input_fingerprint,
    facts_record_version,
    calculation_version,
    receipt_version,
    observation_version,
    COUNT(*)
FROM walk_analyses
GROUP BY 1, 2, 3, 4, 5, 6
HAVING COUNT(*) > 1;

-- 4) payload 종류와 compact cell 수가 metadata와 맞는가
SELECT
    analysis_id,
    paint_fp,
    sheet_schema_version,
    cell_count,
    jsonb_array_length(payload -> 'cells') AS payload_cell_count,
    cell_count = jsonb_array_length(payload -> 'cells') AS cell_count_matches
FROM walk_cellophane_sheets
ORDER BY analysis_id, paint_fp
LIMIT 20;

-- 5) derived인데 분석이나 sheet가 없는 행은 finalize 구현 뒤에도 0이어야 한다
SELECT w.id
FROM walks w
LEFT JOIN walk_analyses a ON a.walk_id = w.id
LEFT JOIN walk_cellophane_sheets s ON s.analysis_id = a.id
WHERE w.analysis_state = 'derived'
GROUP BY w.id
HAVING COUNT(a.id) = 0 OR COUNT(s.analysis_id) = 0;
