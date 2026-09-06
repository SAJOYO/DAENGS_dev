-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — 이유는 verify_2026-09-05_app_user_nickname.sql 머리말과 같다.
-- 사람이 눈으로 볼 질의는 단언 뒤에 남겼다.
DO $verify$
DECLARE
    item record;
    relation regclass;
BEGIN
    IF to_regclass('answer_reports') IS NULL THEN
        RAISE EXCEPTION 'missing table: answer_reports';
    END IF;
    relation := to_regclass('answer_reports');

    FOR item IN SELECT * FROM (VALUES
        ('id', 'uuid', 'true'),
        ('turn_id', 'uuid', 'true'),
        ('app_user_id', 'uuid', 'true'),
        ('reason', 'text', 'true'),
        ('status', 'character varying(20)', 'true'),
        -- 아래 셋은 NULL 이어야 한다. `open` 인 신고는 아직 검토 전이다
        ('reviewed_by', 'uuid', 'false'),
        ('reviewed_at', 'timestamp with time zone', 'false'),
        ('created_at', 'timestamp with time zone', 'true')
    ) AS expected(column_name, type_name, required) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = relation AND a.attname = item.column_name
              AND a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) = item.type_name
              AND a.attnotnull = item.required::boolean
        ) THEN
            RAISE EXCEPTION 'column mismatch: answer_reports.% (type %, not null %)',
                item.column_name, item.type_name, item.required;
        END IF;
    END LOOP;

    -- 제약. **`answer_reports_turn_user_key` 가 이 표의 핵심이다** — 한 사람이 같은 답변을
    -- 두 번 신고하지 못하게 하는 자리이고, 없으면 신고 수가 사람 수가 아니라 클릭 수가 된다.
    --
    -- `reviewed_by` 의 삭제 동작이 `r`(RESTRICT)인 것도 단언한다 — CASCADE 로 바뀌면
    -- 관리자를 지웠을 때 **그 사람이 검토한 신고 기록이 같이 사라진다.**
    FOR item IN SELECT * FROM (VALUES
        ('p', 'id', NULL, NULL, NULL),
        ('u', 'turn_id,app_user_id', NULL, NULL, NULL),
        ('f', 'turn_id', 'chat_turns', 'id', 'c'),
        ('f', 'app_user_id', 'app_users', 'id', 'c'),
        ('f', 'reviewed_by', 'admin_users', 'id', 'r')
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
            RAISE EXCEPTION 'constraint mismatch: answer_reports kind % columns %',
                item.kind, item.columns;
        END IF;
    END LOOP;

    -- CHECK 셋. **`review_state` 가 상태와 검토자를 묶는다** — 없으면 `reviewed` 인데
    -- 검토자가 비어 있는 행이 생기고, 관리자 화면이 "누가 봤나"를 못 보인다.
    FOR item IN SELECT * FROM (VALUES
        ('answer_reports_status_check'),
        ('answer_reports_reason_length_check'),
        ('answer_reports_review_state_check')
    ) AS expected(conname) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint c
            WHERE c.conrelid = relation AND c.conname = item.conname
              AND c.contype = 'c' AND c.convalidated
        ) THEN
            RAISE EXCEPTION 'constraint mismatch: % missing or not validated', item.conname;
        END IF;
    END LOOP;

    -- 인덱스 셋. 목록 화면이 `created_at DESC, id DESC` 로 넘긴다 — 정렬이 빠지면
    -- 페이지 경계에서 같은 행이 두 번 보이거나 건너뛴다.
    FOR item IN SELECT * FROM (VALUES
        ('answer_reports_created_idx'),
        ('answer_reports_status_created_idx'),
        ('answer_reports_turn_idx')
    ) AS expected(index_name) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_index i
            JOIN pg_class c ON c.oid = i.indexrelid
            WHERE i.indrelid = relation AND c.relname = item.index_name
              AND i.indisvalid AND i.indisready
        ) THEN
            RAISE EXCEPTION 'index mismatch: % missing or invalid', item.index_name;
        END IF;
    END LOOP;
END
$verify$;

-- ─────────────────────────────────────────────────────────────────────────
-- 사람이 눈으로 보는 자리. 위 단언이 통과한 뒤에만 여기까지 온다.
-- ─────────────────────────────────────────────────────────────────────────

-- 상태별 신고 수. 적용 직후에는 전부 0이다.
SELECT status, count(*) AS n FROM answer_reports GROUP BY status ORDER BY n DESC;

-- 상태와 검토자가 어긋난 행. **0행이어야 한다** (제약이 막지만 눈으로도 본다).
SELECT count(*) AS inconsistent
FROM answer_reports
WHERE (status = 'open') <> (reviewed_by IS NULL);
