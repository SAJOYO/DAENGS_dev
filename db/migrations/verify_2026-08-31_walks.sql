-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — 이유는 verify_2026-09-05_app_user_nickname.sql 머리말과 같다.
-- 사람이 눈으로 볼 질의는 단언 뒤에 남겼다.
--
-- ⚠️ **이 장의 절반은 뒤 마이그레이션이 걷어 간다.** `verify_<옛것>` 은 `<새것>` 까지 적용된
-- DB 위에서도 돌아야 하는데, 이 파일이 만든 것 중 둘이 나중에 사라진다:
--
--   `walks.pet_id`  → `2026-09-01_walk_pets.sql` 이 다대다 `walk_pets` 로 옮기고 컬럼을 DROP
--   `walk_points`   → `2026-09-02_walk_point_chunks.sql` 이 묶음 표로 옮기고 DROP TABLE
--
-- 그래서 그 둘은 **있을 때만** 검사한다. 없는 것이 정상이고(뒤 장이 돌았다), 있으면
-- 이 장이 만든 모양 그대로여야 한다. 조건 없이 단언하면 운영 DB 전부에서 실패한다.
DO $verify$
DECLARE
    item record;
    relation regclass;
    points regclass;
    definition text;
BEGIN
    IF to_regclass('walks') IS NULL THEN
        RAISE EXCEPTION 'missing table: walks';
    END IF;
    relation := to_regclass('walks');

    -- **거리와 시간은 여기 없다.** 좌표에서 다시 계산하는 값이라 저장하면 계산 규칙을
    -- 고쳤을 때 저장된 숫자와 새로 계산한 숫자가 갈라진다. 아래 목록에 없는 것이 의도다.
    FOR item IN SELECT * FROM (VALUES
        ('id', 'uuid', 'true'),
        ('app_user_id', 'uuid', 'true'),
        ('client_session_id', 'uuid', 'true'),
        ('started_at', 'timestamp with time zone', 'true'),
        -- **끝난 산책만 올라온다.** 기기가 강제 종료되어 열린 채 남은 세션은 기록이 아니라
        -- 사고의 흔적이라 앱이 안 올린다 — 그래서 NOT NULL 이다.
        ('ended_at', 'timestamp with time zone', 'true'),
        -- 날씨 셋은 **못 받았으면 NULL 이고, 그걸 "맑음"으로 채우지 않는다.**
        ('weather_code', 'integer', 'false'),
        ('is_day', 'boolean', 'false'),
        ('temperature_c', 'numeric(4,1)', 'false'),
        ('created_at', 'timestamp with time zone', 'true')
    ) AS expected(column_name, type_name, required) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = relation AND a.attname = item.column_name
              AND a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) = item.type_name
              AND a.attnotnull = item.required::boolean
        ) THEN
            RAISE EXCEPTION 'column mismatch: walks.% (type %, not null %)',
                item.column_name, item.type_name, item.required;
        END IF;
    END LOOP;

    -- PK · 계정 FK · 재업로드 UNIQUE.
    --
    -- `app_user_id` 는 **CASCADE 여야 한다** — 탈퇴가 개인정보를 파기하는데 위치 기록이
    -- 남으면 파기가 반쪽이다. 산책 경로는 집과 생활권을 그대로 드러낸다.
    FOR item IN SELECT * FROM (VALUES
        ('p', 'id', NULL, NULL, NULL),
        ('f', 'app_user_id', 'app_users', 'id', 'c'),
        -- **재시도가 안전해야 한다.** 앱은 네트워크가 끊기면 다음에 다시 올리는데, 그때
        -- 같은 산책이 두 건이 되면 안 된다. 이 제약이 DB 에서 그것을 막는다.
        ('u', 'app_user_id,client_session_id', NULL, NULL, NULL)
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
            RAISE EXCEPTION 'constraint mismatch: walks kind % columns %',
                item.kind, item.columns;
        END IF;
    END LOOP;

    -- 시간 순서 CHECK. 없으면 끝이 시작보다 이른 산책이 들어온다.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        WHERE c.conrelid = relation AND c.conname = 'walks_time_order'
          AND c.contype = 'c' AND c.convalidated
    ) THEN
        RAISE EXCEPTION 'constraint mismatch: walks_time_order missing or not validated';
    END IF;

    -- 목록은 늘 "내 것, 최근 순"이다. 정렬 방향이 뒤집히면 이름은 그대로인 채 거꾸로 읽는다.
    SELECT pg_get_indexdef(i.indexrelid) INTO definition
    FROM pg_index i
    JOIN pg_class c ON c.oid = i.indexrelid
    WHERE i.indrelid = relation AND c.relname = 'walks_owner_started_idx'
      AND i.indisvalid AND i.indisready;
    IF definition IS NULL THEN
        RAISE EXCEPTION 'index mismatch: walks_owner_started_idx missing or invalid';
    END IF;
    IF position('started_at DESC' IN definition) = 0 THEN
        RAISE EXCEPTION 'index mismatch: walks_owner_started_idx lost started_at DESC, got %',
            definition;
    END IF;

    -- ── 여기부터는 **아직 안 걷힌 경우에만** 본다 (머리말 참고) ──────────────────

    -- `walks.pet_id` — `2026-09-01_walk_pets.sql` 이 걷어 간다.
    -- 남아 있다면 **SET NULL 이어야 한다**: 무지개다리를 건넌 아이와의 산책이 그 아이를
    -- 지웠다고 없던 일이 되면 안 된다. 기록은 사람의 것이다. CASCADE 로 뒤바뀌어도
    -- 모양은 멀쩡해 보이는데 그 순간 데이터를 조용히 잃는다.
    IF EXISTS (
        SELECT 1 FROM pg_attribute a
        WHERE a.attrelid = relation AND a.attname = 'pet_id'
          AND a.attnum > 0 AND NOT a.attisdropped
    ) THEN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint c
            WHERE c.conrelid = relation AND c.contype = 'f' AND c.convalidated
              AND ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(num, pos)
                        JOIN pg_attribute a ON a.attrelid = relation AND a.attnum = k.num
                        ORDER BY k.pos) = ARRAY['pet_id']
              AND c.confrelid = to_regclass('pets')
              AND c.confdeltype::text = 'n'
        ) THEN
            RAISE EXCEPTION
                'constraint mismatch: walks.pet_id must be FK -> pets(id) ON DELETE SET NULL';
        END IF;
    END IF;

    -- `walk_points` — `2026-09-02_walk_point_chunks.sql` 이 DROP TABLE 한다.
    -- 남아 있다면 원본 좌표 표의 모양 그대로여야 한다.
    points := to_regclass('walk_points');
    IF points IS NOT NULL THEN
        FOR item IN SELECT * FROM (VALUES
            ('walk_id', 'uuid', 'true'),
            -- 기기가 한 산책 안에서 0부터 매긴 순번. **PK 의 일부다** — 같은 점을 두 번
            -- 보내도 한 줄이다 (앱이 좌표를 나눠 올려도 이 성질이 유지된다).
            ('client_seq', 'integer', 'true'),
            -- 일시정지나 GPS 점프 뒤에 증가한다. 값이 다른 두 점을 직선으로 이으면
            -- 걷지 않은 길이 그려진다.
            ('chain_index', 'integer', 'true'),
            ('at', 'timestamp with time zone', 'true'),
            ('lat', 'numeric(9,6)', 'true'),
            ('lng', 'numeric(9,6)', 'true'),
            ('accuracy_m', 'real', 'false'),
            -- 가상 위치 기록. 지우지 않고 표시만 해 둔다 — 원본이 없으면 나중에 못 가린다.
            ('is_mock', 'boolean', 'true')
        ) AS expected(column_name, type_name, required) LOOP
            IF NOT EXISTS (
                SELECT 1 FROM pg_attribute a
                WHERE a.attrelid = points AND a.attname = item.column_name
                  AND a.attnum > 0 AND NOT a.attisdropped
                  AND format_type(a.atttypid, a.atttypmod) = item.type_name
                  AND a.attnotnull = item.required::boolean
            ) THEN
                RAISE EXCEPTION 'column mismatch: walk_points.% (type %, not null %)',
                    item.column_name, item.type_name, item.required;
            END IF;
        END LOOP;

        FOR item IN SELECT * FROM (VALUES
            ('p', 'walk_id,client_seq', NULL, NULL),
            ('f', 'walk_id', 'walks', 'c')
        ) AS expected(kind, columns, target_table, delete_action) LOOP
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint c
                WHERE c.conrelid = points AND c.contype::text = item.kind
                  AND c.convalidated
                  AND ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(num, pos)
                            JOIN pg_attribute a ON a.attrelid = points AND a.attnum = k.num
                            ORDER BY k.pos) = string_to_array(item.columns, ',')
                  AND (item.kind <> 'f' OR (
                      c.confrelid = to_regclass(item.target_table)
                      AND c.confdeltype::text = item.delete_action
                  ))
            ) THEN
                RAISE EXCEPTION 'constraint mismatch: walk_points kind % columns %',
                    item.kind, item.columns;
            END IF;
        END LOOP;
    END IF;
END
$verify$;

-- ─────────────────────────────────────────────────────────────────────────
-- 사람이 눈으로 보는 자리. 위 단언이 통과한 뒤에만 여기까지 온다.
-- ─────────────────────────────────────────────────────────────────────────

-- 산책 수와, 끝이 시작보다 이른 산책(**0이어야 한다**).
SELECT count(*) AS walks_total,
       count(*) FILTER (WHERE ended_at < started_at) AS bad_time_order,
       count(weather_code) AS with_weather
FROM walks;

-- 뒤 마이그레이션이 걷어 갔는지. 둘 다 NULL 이면 지금 스키마가 최신이라는 뜻이다.
SELECT to_regclass('walk_points') AS walk_points_should_be_null_after_2026_09_02,
       (SELECT count(*) FROM pg_attribute
        WHERE attrelid = to_regclass('walks') AND attname = 'pet_id'
          AND attnum > 0 AND NOT attisdropped) AS walks_pet_id_should_be_0_after_2026_09_01;
