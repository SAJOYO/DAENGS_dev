-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — 이유는 verify_2026-09-05_app_user_nickname.sql 머리말과 같다.
-- 사람이 눈으로 볼 질의는 단언 뒤에 남겼다.
--
-- 이 장이 지키는 것 둘:
--   ⓐ **두 상태 축이 안 섞이는 것** — `status`(파이프라인이 어디까지 갔나)와
--      `quality_status`(분석해 보니 쓸 만한가)는 다른 축이다. FAILED(워커가 죽음 → 재시도)와
--      unavailable(영상이 분석 부적합 → 재촬영)은 **사용자 안내가 완전히 다르다**
--      (D-033 이 ABSTAINED ≠ REFUSED 를 가른 것과 같은 이유).
--   ⓑ **JSONB 세 칸이 안 합쳐지는 것** — `internal_feature_vector` 는 비교 전용이고
--      **API 응답이 절대 내보내면 안 된다.** 한 컬럼에 섞으면 실수로 나간다.
DO $verify$
DECLARE
    item record;
    relation regclass;
    definition text;
BEGIN
    IF to_regclass('gait_records') IS NULL THEN
        RAISE EXCEPTION 'missing table: gait_records';
    END IF;
    relation := to_regclass('gait_records');

    FOR item IN SELECT * FROM (VALUES
        ('id', 'uuid', 'true'),
        ('pet_id', 'uuid', 'true'),
        ('status', 'character varying(20)', 'true'),
        -- DONE 일 때만 채워지므로 nullable 이다.
        ('quality_status', 'character varying(20)', 'false'),
        ('quality_tier', 'character varying(10)', 'false'),
        -- **영상 바이트는 DB 에 절대 안 들어간다.** 이 둘은 클라우드 저장소(#78)의 불투명
        -- 식별자다. provider 가 안 정해져 형식을 강제하지 않는다(text).
        ('original_storage_key', 'text', 'false'),
        ('overlay_storage_key', 'text', 'false'),
        -- ⓑ 세 칸이 **각각** 있어야 한다. 아래에 그 이유를 다시 단언한다.
        ('quality', 'jsonb', 'false'),
        ('summary_for_ui', 'jsonb', 'false'),
        ('internal_feature_vector', 'jsonb', 'false'),
        ('gait_filter_version', 'text', 'false'),
        ('captured_at', 'date', 'false'),
        ('video_meta', 'jsonb', 'false'),
        ('source_file', 'text', 'false'),
        ('note', 'text', 'false'),
        ('failure_reason', 'text', 'false'),
        ('created_at', 'timestamp with time zone', 'true'),
        ('updated_at', 'timestamp with time zone', 'true'),
        -- soft delete. 파일 삭제(스토리지)가 비동기라, 행을 먼저 지우면 키를 잃어
        -- 파일이 영영 고아가 된다.
        ('deleted_at', 'timestamp with time zone', 'false')
    ) AS expected(column_name, type_name, required) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = relation AND a.attname = item.column_name
              AND a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) = item.type_name
              AND a.attnotnull = item.required::boolean
        ) THEN
            RAISE EXCEPTION 'column mismatch: gait_records.% (type %, not null %)',
                item.column_name, item.type_name, item.required;
        END IF;
    END LOOP;

    -- ⓑ 를 한 번 더, 이름으로 못 박는다. 위 목록은 "타입이 맞나"를 보는데, 세 칸을 하나로
    -- 합치는 변조는 **남은 칸의 타입이 그대로라** 그것만으로는 안 잡힐 수 있다.
    FOR item IN SELECT * FROM (VALUES
        ('quality'), ('summary_for_ui'), ('internal_feature_vector')
    ) AS expected(column_name) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = relation AND a.attname = item.column_name
              AND a.attnum > 0 AND NOT a.attisdropped
        ) THEN
            RAISE EXCEPTION
                'column mismatch: gait_records.% is gone — 셋을 한 칸에 합치면 '
                'internal_feature_vector 가 API 응답으로 새어 나간다', item.column_name;
        END IF;
    END LOOP;

    -- PK · FK. **CASCADE 여야 한다** — 소유권은 pet_id → pets.app_user_id 로 유도하므로,
    -- 강아지가 사라진 기록은 주인을 못 찾는 영상이 된다.
    FOR item IN SELECT * FROM (VALUES
        ('p', 'id', NULL, NULL),
        ('f', 'pet_id', 'pets', 'c')
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
            RAISE EXCEPTION 'constraint mismatch: gait_records kind % columns %',
                item.kind, item.columns;
        END IF;
    END LOOP;

    -- ⓐ CHECK 셋. 값 목록까지 본다 — 하나가 빠지면 그 상태의 행을 아예 못 넣는데,
    -- 워커는 그때 기록만 실패하고 분석은 마친다.
    FOR item IN SELECT * FROM (VALUES
        ('gait_records_status_check', 'PENDING,UPLOADED,PROCESSING,DONE,FAILED'),
        ('gait_records_quality_status_check', 'ok,unavailable'),
        ('gait_records_quality_tier_check', 'good,low')
    ) AS expected(conname, values_csv) LOOP
        SELECT pg_get_constraintdef(c.oid) INTO definition
        FROM pg_constraint c
        WHERE c.conrelid = relation AND c.conname = item.conname
          AND c.contype = 'c' AND c.convalidated;
        IF definition IS NULL THEN
            RAISE EXCEPTION 'constraint mismatch: % missing or not validated', item.conname;
        END IF;
        IF EXISTS (
            SELECT 1 FROM unnest(string_to_array(item.values_csv, ',')) AS v(value)
            WHERE position('''' || v.value || '''' IN definition) = 0
        ) THEN
            RAISE EXCEPTION 'constraint mismatch: % lost one of (%), got %',
                item.conname, item.values_csv, definition;
        END IF;
    END LOOP;

    -- 목록 조회가 "이 강아지의 기록, 시간순" 하나뿐이다 (설계문서 §4).
    IF NOT EXISTS (
        SELECT 1 FROM pg_index i
        JOIN pg_class c ON c.oid = i.indexrelid
        WHERE i.indrelid = relation AND c.relname = 'idx_gait_records_pet_created'
          AND i.indisvalid AND i.indisready
    ) THEN
        RAISE EXCEPTION 'index mismatch: idx_gait_records_pet_created missing or invalid';
    END IF;

    -- `updated_at` 트리거. 없으면 그 칸이 만든 시각에서 영영 멈춘다 — 조용히 틀린다.
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
        WHERE t.tgrelid = relation AND t.tgname = 'trg_gait_records_updated_at'
          AND NOT t.tgisinternal
    ) THEN
        RAISE EXCEPTION 'trigger mismatch: trg_gait_records_updated_at missing';
    END IF;
END
$verify$;

-- ─────────────────────────────────────────────────────────────────────────
-- 사람이 눈으로 보는 자리. 위 단언이 통과한 뒤에만 여기까지 온다.
-- ─────────────────────────────────────────────────────────────────────────

-- 상태별 기록 수. 옛 JSON 은 이관하지 않았으므로 적용 직후에는 0이다.
SELECT status, count(*) AS records, count(*) FILTER (WHERE deleted_at IS NOT NULL) AS soft_deleted
FROM gait_records
GROUP BY status
ORDER BY records DESC;

-- 두 축이 어긋난 행. DONE 이 아닌데 품질이 찍혔거나, DONE 인데 안 찍힌 것.
-- CHECK 이 막지 않는 조합이라 눈으로 본다.
SELECT count(*) FILTER (WHERE status <> 'DONE' AND quality_status IS NOT NULL) AS graded_too_early,
       count(*) FILTER (WHERE status = 'DONE' AND quality_status IS NULL) AS done_but_ungraded
FROM gait_records;
