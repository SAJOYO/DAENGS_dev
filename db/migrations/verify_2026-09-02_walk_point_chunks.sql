-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — 이유는 verify_2026-09-05_app_user_nickname.sql 머리말과 같다.
-- 사람이 눈으로 볼 질의는 단언 뒤에 남겼다.
-- (2026-09-07 #295 에 단언형으로 다시 썼다. 그 전에는 SELECT 나열이라 **틀려도 녹색**이었다.
--  하필 이 파일이 그 상태의 대표였다 — 옛 표가 남았는지를 `SELECT to_regclass(...)` 로
--  **찍기만** 했고, 남아 있어도 통과했다.)
--
-- 좌표 한 점에 한 줄이던 `walk_points` 를 묶음으로 옮긴 장이다. 실기기 실측으로 초당
-- 1.02점이 쌓였다.
DO $verify$
DECLARE
    item record;
    relation regclass;
BEGIN
    IF to_regclass('walk_point_chunks') IS NULL THEN
        RAISE EXCEPTION 'missing table: walk_point_chunks';
    END IF;
    relation := to_regclass('walk_point_chunks');

    FOR item IN SELECT * FROM (VALUES
        ('walk_id', 'uuid', 'true'),
        ('seq_from', 'integer', 'true'),
        ('seq_to', 'integer', 'true'),
        ('point_count', 'integer', 'true'),
        -- 점들이 여기 들어간다. `{v, cols, pts}` 모양이고 `pts` 길이가 `point_count` 다.
        ('payload', 'jsonb', 'true')
    ) AS expected(column_name, type_name, required) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = relation AND a.attname = item.column_name
              AND a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) = item.type_name
              AND a.attnotnull = item.required::boolean
        ) THEN
            RAISE EXCEPTION 'column mismatch: walk_point_chunks.% (type %, not null %)',
                item.column_name, item.type_name, item.required;
        END IF;
    END LOOP;

    -- PK 는 `(walk_id, seq_from)` 이다. **묶음의 시작 번호가 신원의 일부**라, 같은 묶음을
    -- 두 번 올려도 한 줄이다 (`walk_points` 의 `(walk_id, client_seq)` 가 하던 일과 같다).
    -- FK 는 CASCADE — 산책이 지워지면 좌표도 지워져야 한다(위치 기록이라 파기가 반쪽이면 안 된다).
    FOR item IN SELECT * FROM (VALUES
        ('p', 'walk_id,seq_from', NULL, NULL),
        ('f', 'walk_id', 'walks', 'c')
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
            RAISE EXCEPTION 'constraint mismatch: walk_point_chunks kind % columns %',
                item.kind, item.columns;
        END IF;
    END LOOP;

    -- CHECK 둘. 빈 묶음과 뒤집힌 구간을 막는다 — 둘 다 들어오면 아래 "사람이 보는 자리"의
    -- 대조(`pts` 길이 = `point_count`)가 성립하지 않는다.
    FOR item IN SELECT * FROM (VALUES
        ('walk_point_chunks_point_count_check'), ('walk_point_chunks_seq_order')
    ) AS expected(conname) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint c
            WHERE c.conrelid = relation AND c.conname = item.conname
              AND c.contype = 'c' AND c.convalidated
        ) THEN
            RAISE EXCEPTION 'constraint mismatch: % missing or not validated', item.conname;
        END IF;
    END LOOP;

    -- **옛 표가 사라졌어야 한다 — 이 파일이 SELECT 나열이던 시절 못 잡던 자리다.**
    --
    -- 마이그레이션은 `walk_points` 가 없으면 조용히 넘어가고(새 DB 에서는 `db/init` 이 이미
    -- 새 표를 만든다), 있으면 옮긴 뒤 `DROP TABLE` 한다. 옮기기만 하고 DROP 이 안 되면
    -- **같은 좌표가 두 곳에 있고** 어느 쪽이 정본인지 아무도 모른다.
    IF to_regclass('walk_points') IS NOT NULL THEN
        RAISE EXCEPTION 'table mismatch: walk_points must be dropped '
                        '(옮기기만 하고 DROP TABLE 을 빠뜨리면 좌표가 두 곳에 남는다)';
    END IF;
END
$verify$;

-- ─────────────────────────────────────────────────────────────────────────
-- 사람이 눈으로 보는 자리. 위 단언이 통과한 뒤에만 여기까지 온다.
-- ─────────────────────────────────────────────────────────────────────────

-- 산책마다 몇 묶음 · 몇 점인가.
SELECT (SELECT count(*) FROM walks) AS walks,
       (SELECT count(*) FROM walk_point_chunks) AS chunks,
       (SELECT COALESCE(SUM(point_count), 0) FROM walk_point_chunks) AS points;

-- payload 모양이 약속대로인가. **`mismatched` 는 0이어야 한다** — 옮기다 자리가 밀리면
-- 여기서 드러난다 (CHECK 으로는 못 막는 자리다).
SELECT count(*) AS chunks_checked,
       count(*) FILTER (WHERE jsonb_array_length(payload -> 'pts') <> point_count) AS mismatched,
       count(*) FILTER (WHERE (payload -> 'pts' -> 0 ->> 0)::INT <> seq_from) AS first_seq_off,
       count(*) FILTER (WHERE (payload -> 'pts' -> (point_count - 1) ->> 0)::INT <> seq_to)
           AS last_seq_off
FROM walk_point_chunks;

-- 좌표가 한국 범위 안인가 (옮기다 위도·경도가 바뀌지 않았는지).
SELECT MIN((pt ->> 3)::NUMERIC) AS min_lat, MAX((pt ->> 3)::NUMERIC) AS max_lat,
       MIN((pt ->> 4)::NUMERIC) AS min_lng, MAX((pt ->> 4)::NUMERIC) AS max_lng
FROM walk_point_chunks, LATERAL jsonb_array_elements(payload -> 'pts') AS pt;
