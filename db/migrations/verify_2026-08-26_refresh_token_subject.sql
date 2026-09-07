-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — 이유는 verify_2026-09-05_app_user_nickname.sql 머리말과 같다.
-- 사람이 눈으로 볼 질의는 단언 뒤에 남겼다.
--
-- 이 장이 지키는 것은 **"세션 행에는 주인이 정확히 하나"** 다. 마이그레이션이 관리자
-- 컬럼의 NOT NULL 을 푸는데(앱 회원 행에서는 그 칸이 비어야 하므로), 그 자리를 CHECK 가
-- 대신 지킨다. **둘 중 하나만 하면 주인 없는 세션 행이 만들어진다** — 그때 강제 로그아웃이
-- 아무도 못 지우는 토큰을 남긴다.
DO $verify$
DECLARE
    item record;
    relation regclass;
    definition text;
BEGIN
    IF to_regclass('refresh_tokens') IS NULL THEN
        RAISE EXCEPTION 'missing table: refresh_tokens';
    END IF;
    relation := to_regclass('refresh_tokens');

    -- **둘 다 nullable 이어야 한다.** `admin_user_id` 는 원래 NOT NULL 이었고 이 파일이 푼다.
    FOR item IN SELECT * FROM (VALUES
        ('admin_user_id', 'uuid', 'false'),
        ('app_user_id', 'uuid', 'false')
    ) AS expected(column_name, type_name, required) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = relation AND a.attname = item.column_name
              AND a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) = item.type_name
              AND a.attnotnull = item.required::boolean
        ) THEN
            RAISE EXCEPTION 'column mismatch: refresh_tokens.% (type %, not null %)',
                item.column_name, item.type_name, item.required;
        END IF;
    END LOOP;

    -- FK 둘. **삭제 동작이 둘 다 CASCADE** 다 — 계정이 사라지면 그 계정의 세션도 사라져야 한다.
    FOR item IN SELECT * FROM (VALUES
        ('f', 'admin_user_id', 'admin_users', 'id', 'c'),
        ('f', 'app_user_id', 'app_users', 'id', 'c')
    ) AS expected(kind, columns, target_table, target_columns, delete_action) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint c
            WHERE c.conrelid = relation AND c.contype::text = item.kind
              AND c.convalidated
              AND ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(num, pos)
                        JOIN pg_attribute a ON a.attrelid = relation AND a.attnum = k.num
                        ORDER BY k.pos) = string_to_array(item.columns, ',')
              AND c.confrelid = to_regclass(item.target_table)
              AND c.confdeltype::text = item.delete_action
              AND ARRAY(SELECT a.attname::text FROM unnest(c.confkey) WITH ORDINALITY k(num, pos)
                        JOIN pg_attribute a ON a.attrelid = c.confrelid AND a.attnum = k.num
                        ORDER BY k.pos) = string_to_array(item.target_columns, ',')
        ) THEN
            RAISE EXCEPTION 'constraint mismatch: refresh_tokens fk % -> %(%) on delete %',
                item.columns, item.target_table, item.target_columns, item.delete_action;
        END IF;
    END LOOP;

    -- **이 파일의 핵심 단언.** NOT NULL 을 푼 자리를 이 CHECK 가 대신 지킨다.
    -- 떼면 타입도 널 허용도 그대로라 위의 컬럼 단언은 전부 통과하는데, 그 순간부터
    -- 주인이 없는(둘 다 NULL) 세션 행과 주인이 둘인 행이 만들어질 수 있다.
    SELECT pg_get_constraintdef(c.oid) INTO definition
    FROM pg_constraint c
    WHERE c.conrelid = relation AND c.conname = 'refresh_tokens_one_subject_check'
      AND c.contype = 'c' AND c.convalidated;
    IF definition IS NULL THEN
        RAISE EXCEPTION
            'constraint mismatch: refresh_tokens_one_subject_check missing or not validated';
    END IF;
    -- 정의 문자열을 통째로 비교하지 않는다 (README 의 함정 ①) — Postgres 가 다시 써서
    -- 내놓기 때문이다. 함수 이름과 "= 1" 만 본다.
    IF position('num_nonnulls' IN definition) = 0 OR position('= 1' IN definition) = 0 THEN
        RAISE EXCEPTION 'constraint mismatch: one_subject_check is not num_nonnulls(...) = 1, got %',
            definition;
    END IF;

    -- 인덱스 둘은 **부분 인덱스여야 한다.** 각 행이 둘 중 한 칸만 채우므로, 전체 인덱스면
    -- NULL 이 절반씩 실린다. 마이그레이션이 옛 전체 인덱스 `idx_refresh_tokens_admin` 을
    -- 일부러 DROP 하고 부분으로 다시 만드는 것이 그 이유다 — 이름이 같아서 **다시 만들기를
    -- 빠뜨려도 "인덱스가 있다"는 확인은 통과한다.**
    FOR item IN SELECT * FROM (VALUES
        ('idx_refresh_tokens_admin', 'admin_user_id IS NOT NULL'),
        ('idx_refresh_tokens_app', 'app_user_id IS NOT NULL')
    ) AS expected(index_name, predicate) LOOP
        SELECT pg_get_indexdef(i.indexrelid) INTO definition
        FROM pg_index i
        JOIN pg_class c ON c.oid = i.indexrelid
        WHERE i.indrelid = relation AND c.relname = item.index_name
          AND i.indisvalid AND i.indisready;
        IF definition IS NULL THEN
            RAISE EXCEPTION 'index mismatch: % missing or invalid', item.index_name;
        END IF;
        IF position(item.predicate IN definition) = 0 THEN
            RAISE EXCEPTION 'index mismatch: % lost its WHERE %, got %',
                item.index_name, item.predicate, definition;
        END IF;
    END LOOP;
END
$verify$;

-- ─────────────────────────────────────────────────────────────────────────
-- 사람이 눈으로 보는 자리. 위 단언이 통과한 뒤에만 여기까지 온다.
-- ─────────────────────────────────────────────────────────────────────────

-- 주인별 세션 수. `orphaned` 는 **0이어야 한다** (CHECK 가 막지만 눈으로도 본다).
SELECT count(*) AS tokens_total,
       count(admin_user_id) AS admin_sessions,
       count(app_user_id) AS app_sessions,
       count(*) FILTER (WHERE num_nonnulls(admin_user_id, app_user_id) <> 1) AS orphaned
FROM refresh_tokens;
