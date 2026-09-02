-- verify_2026-09-02_walk_point_chunks.sql
-- 적용 뒤 눈으로 확인하는 질의 모음. 고치는 것은 없다.

-- 1) 표가 생겼고 옛 표는 사라졌나
SELECT
    to_regclass('public.walk_point_chunks') AS chunks_table,
    to_regclass('public.walk_points')       AS old_table_should_be_null;

-- 2) 산책마다 몇 묶음 · 몇 점인가. walks 수와 대조한다.
SELECT
    (SELECT COUNT(*) FROM walks)                        AS walks,
    (SELECT COUNT(*) FROM walk_point_chunks)            AS chunks,
    (SELECT COALESCE(SUM(point_count), 0) FROM walk_point_chunks) AS points;

-- 3) payload 모양이 약속대로인가 (v · cols · pts 길이가 point_count 와 같은지)
SELECT
    walk_id,
    seq_from,
    seq_to,
    point_count,
    payload -> 'v'                              AS version,
    jsonb_array_length(payload -> 'cols')       AS col_count,
    jsonb_array_length(payload -> 'pts')        AS pts_len,
    jsonb_array_length(payload -> 'pts') = point_count AS pts_len_matches
FROM walk_point_chunks
ORDER BY walk_id, seq_from
LIMIT 20;

-- 4) 첫 점과 마지막 점의 순번이 seq_from · seq_to 와 맞는가
SELECT
    walk_id,
    seq_from,
    (payload -> 'pts' -> 0 ->> 0)::INT                                        AS first_seq,
    seq_to,
    (payload -> 'pts' -> (point_count - 1) ->> 0)::INT                        AS last_seq
FROM walk_point_chunks
ORDER BY walk_id, seq_from
LIMIT 20;

-- 5) 좌표가 한국 범위 안인가 (옮기다 자리가 밀리지 않았는지)
SELECT
    MIN((pt ->> 3)::NUMERIC) AS min_lat,
    MAX((pt ->> 3)::NUMERIC) AS max_lat,
    MIN((pt ->> 4)::NUMERIC) AS min_lng,
    MAX((pt ->> 4)::NUMERIC) AS max_lng
FROM walk_point_chunks, LATERAL jsonb_array_elements(payload -> 'pts') AS pt;
