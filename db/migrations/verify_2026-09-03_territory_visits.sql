-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — 이유는 verify_2026-09-05_app_user_nickname.sql 머리말과 같다.
-- 사람이 눈으로 볼 질의는 단언 뒤에 남겼다.
-- (2026-09-07 #295 에 단언형으로 다시 썼다. 그 전에는 SELECT 나열이라 **틀려도 녹색**이었다.)
--
-- 점령 게임의 "정말 거기 갔다"를 남기는 표다. 여기 걸린 제약은 대부분 **부정행위를 막는
-- 규칙**이라, 하나가 풀려도 화면은 멀쩡히 돌고 점수만 조용히 틀린다.
DO $verify$
DECLARE
    item record;
    visits regclass;
    attempts regclass;
    definition text;
BEGIN
    IF to_regclass('territory_verified_visits') IS NULL THEN
        RAISE EXCEPTION 'missing table: territory_verified_visits';
    END IF;
    IF to_regclass('territory_attempts') IS NULL THEN
        RAISE EXCEPTION 'missing table: territory_attempts';
    END IF;
    visits := to_regclass('territory_verified_visits');
    attempts := to_regclass('territory_attempts');

    FOR item IN SELECT * FROM (VALUES
        ('id', 'uuid', 'true'),
        ('attempt_id', 'uuid', 'true'),
        ('evidence_version', 'integer', 'true'),
        ('verified_at', 'timestamp with time zone', 'true')
    ) AS expected(column_name, type_name, required) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = visits AND a.attname = item.column_name
              AND a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) = item.type_name
              AND a.attnotnull = item.required::boolean
        ) THEN
            RAISE EXCEPTION 'column mismatch: territory_verified_visits.% (type %, not null %)',
                item.column_name, item.type_name, item.required;
        END IF;
    END LOOP;

    -- **`attempt_id` 가 UNIQUE 인 것이 이 표의 전부다.** 시도 하나에 인정 방문은 하나다 —
    -- 풀리면 같은 사진 한 장으로 같은 자리를 여러 번 인정받을 수 있다. PK 는 따로 있어서
    -- **UNIQUE 만 사라져도 스키마는 멀쩡해 보인다.**
    FOR item IN SELECT * FROM (VALUES
        ('p', 'id', NULL, NULL),
        ('u', 'attempt_id', NULL, NULL),
        ('f', 'attempt_id', 'territory_attempts', 'c')
    ) AS expected(kind, columns, target_table, delete_action) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint c
            WHERE c.conrelid = visits AND c.contype::text = item.kind
              AND c.convalidated
              AND ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(num, pos)
                        JOIN pg_attribute a ON a.attrelid = visits AND a.attnum = k.num
                        ORDER BY k.pos) = string_to_array(item.columns, ',')
              AND (item.kind <> 'f' OR (
                  c.confrelid = to_regclass(item.target_table)
                  AND c.confdeltype::text = item.delete_action
              ))
              AND (item.kind NOT IN ('p', 'u') OR EXISTS (
                  SELECT 1 FROM pg_index i WHERE i.indexrelid = c.conindid
                    AND i.indisvalid AND i.indisready AND i.indisunique
              ))
        ) THEN
            RAISE EXCEPTION 'constraint mismatch: territory_verified_visits kind % columns % '
                            '(attempt_id 의 UNIQUE 가 풀리면 사진 한 장으로 여러 번 인정된다)',
                item.kind, item.columns;
        END IF;
    END LOOP;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        WHERE c.conrelid = visits AND c.conname = 'territory_verified_visits_evidence_version_positive'
          AND c.contype = 'c' AND c.convalidated
    ) THEN
        RAISE EXCEPTION 'constraint mismatch: evidence_version_positive missing or not validated';
    END IF;

    -- ── 이 장이 `territory_attempts` 에 더한 제약들 ──────────────────────
    -- 전부 **거리·위치의 진짜임**을 지키는 규칙이라 하나가 풀려도 조용하다.
    FOR item IN SELECT * FROM (VALUES
        -- 가상 위치로 만든 시도는 아예 못 들어온다. `is_mock` 칸만 있고 CHECK 이 없으면
        -- 표시만 되고 걸러지지 않는다.
        ('territory_attempts_not_mock'),
        -- **정확도까지 더해 10m 안**이어야 한다. `distance_m` 만 보면 오차 50m 짜리 측정이
        -- "1m 앞"으로 통과한다 — 이 제약이 그 구멍을 막는다.
        ('territory_attempts_location_evidence'),
        ('territory_attempts_distance_range'),
        ('territory_attempts_capture_coordinate_range'),
        ('territory_attempts_site_coordinate_range'),
        -- 확정된 시도는 사진의 신원(generation · 크기)이 있어야 한다.
        ('territory_attempts_confirmed_photo_identity'),
        -- 최종 상태 셋은 어느 모델이 판정했는지를 남겨야 한다.
        ('territory_attempts_final_vision_metadata'),
        ('territory_attempts_photo_type_check'),
        ('territory_attempts_status_check')
    ) AS expected(conname) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint c
            WHERE c.conrelid = attempts AND c.conname = item.conname
              AND c.contype = 'c' AND c.convalidated
        ) THEN
            RAISE EXCEPTION 'constraint mismatch: % missing or not validated', item.conname;
        END IF;
    END LOOP;

    -- 상태 다섯이 다 있어야 한다. 하나가 빠지면 그 결말의 시도를 아예 못 적는다.
    SELECT pg_get_constraintdef(c.oid) INTO definition
    FROM pg_constraint c
    WHERE c.conrelid = attempts AND c.conname = 'territory_attempts_status_check';
    FOR item IN SELECT * FROM (VALUES
        ('PENDING_UPLOAD'), ('VISION_PENDING'), ('VERIFIED'), ('REJECTED'), ('FAILED')
    ) AS expected(value) LOOP
        IF position('''' || item.value || '''' IN definition) = 0 THEN
            RAISE EXCEPTION 'constraint mismatch: territory_attempts status lost %, got %',
                item.value, definition;
        END IF;
    END LOOP;

    -- 재업로드가 시도를 두 배로 만들지 않는다. 사진 키도 유일해야 한다 —
    -- 같은 사진을 다른 시도에 붙이는 길을 막는다.
    FOR item IN SELECT * FROM (VALUES
        ('u', 'app_user_id,client_capture_id'),
        ('u', 'photo_storage_key')
    ) AS expected(kind, columns) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint c
            WHERE c.conrelid = attempts AND c.contype::text = item.kind AND c.convalidated
              AND ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(num, pos)
                        JOIN pg_attribute a ON a.attrelid = attempts AND a.attnum = k.num
                        ORDER BY k.pos) = string_to_array(item.columns, ',')
        ) THEN
            RAISE EXCEPTION 'constraint mismatch: territory_attempts unique % missing',
                item.columns;
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

SELECT count(*) AS invalid_attempt_state
FROM territory_attempts
WHERE status NOT IN ('PENDING_UPLOAD','VISION_PENDING','VERIFIED','REJECTED','FAILED')
   OR distance_m < 0
   OR distance_m > 10
   OR is_mock
   OR photo_content_type NOT IN ('image/jpeg','image/webp');

SELECT count(*) AS terminal_photo_cleanup_pending
FROM territory_attempts
WHERE status IN ('VERIFIED', 'REJECTED', 'FAILED')
  AND photo_redacted_at IS NULL;

SELECT count(*) AS confirmed_without_photo_identity
FROM territory_attempts
WHERE status <> 'PENDING_UPLOAD'
  AND (
      photo_object_generation IS NULL
      OR btrim(photo_object_generation) = ''
      OR photo_size_bytes NOT BETWEEN 1 AND 12582912
  );

SELECT count(*) AS location_uncertainty_outside_radius
FROM territory_attempts
WHERE accuracy_m IS NULL
   OR accuracy_m < 0
   OR distance_m + accuracy_m > 10;

SELECT count(*) AS final_without_vision_metadata
FROM territory_attempts
WHERE status IN ('VERIFIED', 'REJECTED', 'FAILED')
  AND (
      vision_model IS NULL OR btrim(vision_model) = ''
      OR vision_model_version IS NULL OR btrim(vision_model_version) = ''
  );

SELECT count(*) AS verified_without_fact
FROM territory_attempts AS attempt
LEFT JOIN territory_verified_visits AS visit ON visit.attempt_id = attempt.id
WHERE attempt.status = 'VERIFIED'
  AND visit.id IS NULL;

SELECT count(*) AS fact_for_nonverified_attempt
FROM territory_verified_visits AS visit
JOIN territory_attempts AS attempt ON attempt.id = visit.attempt_id
WHERE attempt.status <> 'VERIFIED';
