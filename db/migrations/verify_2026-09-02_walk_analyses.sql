-- verify_2026-09-02_walk_analyses.sql
-- 적용 뒤 눈으로 확인하는 읽기 전용 질의 모음.

-- 1) 컬럼과 두 테이블이 생겼나
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
