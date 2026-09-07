-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — 이유는 verify_2026-09-05_app_user_nickname.sql 머리말과 같다.
-- 사람이 눈으로 볼 질의는 단언 뒤에 남겼다.
-- (2026-09-07 #295 에 단언형으로 다시 썼다. 그 전에는 SELECT 나열이라 **틀려도 녹색**이었다.)
--
-- 관리자가 무엇을 했는지 남기는 기록이다. **로그가 아니라 데이터다** — 운영 로그(에러 ·
-- 스택트레이스)는 파일로 가고 여기 안 넣는다 (`docs/console/roadmap.md` §6).
-- 그래서 이 표에서 지켜야 할 것이 다른 표와 반대인 자리가 둘 있고, 아래 ①·② 가 그것이다.
DO $verify$
DECLARE
    item record;
    relation regclass;
    definition text;
BEGIN
    IF to_regclass('admin_audit_log') IS NULL THEN
        RAISE EXCEPTION 'missing table: admin_audit_log';
    END IF;
    relation := to_regclass('admin_audit_log');

    FOR item IN SELECT * FROM (VALUES
        ('id', 'uuid', 'true'),
        -- ① **NULL 을 허용해야 한다 — 로그인 실패 때문이다.** 없는 아이디로 두드린 시도는
        --    가리킬 `admin_users` 행이 아예 없다. NOT NULL 을 걸면 **그 시도가 통째로
        --    기록되지 않는다** — 감사 로그에서 가장 보고 싶은 행이 사라진다.
        ('admin_user_id', 'uuid', 'false'),
        -- CHECK 로 안 묶는다 — 카드마다 느는 목록이라 묶으면 화면 하나에 ALTER 가 둘이다.
        ('action', 'character varying(60)', 'true'),
        -- 대상이 없는 행위(로그인)는 둘 다 NULL 이다. FK 도 없다 — 가리키는 표가
        -- `target_type` 에 따라 달라진다.
        ('target_type', 'character varying(30)', 'false'),
        ('target_id', 'uuid', 'false'),
        ('detail', 'jsonb', 'false'),
        ('request_id', 'character varying(64)', 'false'),
        ('ip', 'inet', 'false'),
        ('created_at', 'timestamp with time zone', 'true')
    ) AS expected(column_name, type_name, required) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = relation AND a.attname = item.column_name
              AND a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) = item.type_name
              AND a.attnotnull = item.required::boolean
        ) THEN
            RAISE EXCEPTION 'column mismatch: admin_audit_log.% (type %, not null %)',
                item.column_name, item.type_name, item.required;
        END IF;
    END LOOP;

    -- ② **FK 의 삭제 동작이 `RESTRICT` 여야 한다 — 이 파일에서 가장 중요한 단언이다.**
    --
    -- `refresh_tokens` 의 CASCADE 와 **반대이고 그것이 의도**다: 세션은 없어져야 하고
    -- 기록은 남아야 한다. CASCADE 로 바뀌면 **관리자를 지우는 순간 그 사람이 한 일이
    -- 통째로 사라진다** — 감사 로그가 가장 필요한 상황에서 정확히 그것이 없어진다.
    -- 모양은 멀쩡해 보이고 화면도 그대로 돈다.
    --
    -- 설계는 admin_users 를 지우지 않고 `suspended` 로 막는 것인데, 이 제약이 DB 로
    -- 그것을 강제한다.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        WHERE c.conrelid = relation AND c.contype = 'f' AND c.convalidated
          AND ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(num, pos)
                    JOIN pg_attribute a ON a.attrelid = relation AND a.attnum = k.num
                    ORDER BY k.pos) = ARRAY['admin_user_id']
          AND c.confrelid = to_regclass('admin_users')
          AND c.confdeltype::text = 'r'
    ) THEN
        RAISE EXCEPTION 'constraint mismatch: admin_audit_log.admin_user_id must be '
                        'FK -> admin_users(id) ON DELETE RESTRICT '
                        '(CASCADE 면 관리자를 지울 때 그 사람의 기록이 같이 사라진다)';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        WHERE c.conrelid = relation AND c.contype = 'p' AND c.convalidated
    ) THEN
        RAISE EXCEPTION 'constraint mismatch: admin_audit_log has no primary key';
    END IF;

    -- `detail` 은 객체만. **복호화된 개인정보를 넣지 않는다** — "무엇을 열었나"까지다.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        WHERE c.conrelid = relation AND c.conname = 'admin_audit_log_detail_object_check'
          AND c.contype = 'c' AND c.convalidated
    ) THEN
        RAISE EXCEPTION 'constraint mismatch: admin_audit_log_detail_object_check '
                        'missing or not validated';
    END IF;

    -- ③ 인덱스 셋. **뒤의 둘은 부분 인덱스다** — 주체가 없는 행(로그인 실패)과 대상이
    --    없는 행(로그인)을 각각 빼는 것이 그 인덱스의 뜻이다. `WHERE` 가 빠지면
    --    이름은 그대로인 채 NULL 이 잔뜩 실린다.
    FOR item IN SELECT * FROM (VALUES
        ('idx_admin_audit_log_created', 'created_at DESC'),
        ('idx_admin_audit_log_admin', 'admin_user_id IS NOT NULL'),
        ('idx_admin_audit_log_target', 'target_id IS NOT NULL')
    ) AS expected(index_name, fragment) LOOP
        SELECT pg_get_indexdef(i.indexrelid) INTO definition
        FROM pg_index i
        JOIN pg_class c ON c.oid = i.indexrelid
        WHERE i.indrelid = relation AND c.relname = item.index_name
          AND i.indisvalid AND i.indisready;
        IF definition IS NULL THEN
            RAISE EXCEPTION 'index mismatch: % missing or invalid', item.index_name;
        END IF;
        IF position(item.fragment IN definition) = 0 THEN
            RAISE EXCEPTION 'index mismatch: % lost %, got %',
                item.index_name, item.fragment, definition;
        END IF;
    END LOOP;

    -- ④ **`updated_at` 도 트리거도 없어야 한다.** append-only 라서다 — 다른 표에 다 있는
    --    것이 여기만 없는 것은 빠뜨린 게 아니다. 누가 나중에 "통일"하려고 더하면
    --    감사 기록을 고칠 수 있게 된다.
    IF EXISTS (
        SELECT 1 FROM pg_attribute a
        WHERE a.attrelid = relation AND a.attname = 'updated_at'
          AND a.attnum > 0 AND NOT a.attisdropped
    ) THEN
        RAISE EXCEPTION 'column mismatch: admin_audit_log must not have updated_at '
                        '(append-only 다 — 있으면 감사 기록을 고칠 수 있게 된다)';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_trigger t
        WHERE t.tgrelid = relation AND NOT t.tgisinternal
    ) THEN
        RAISE EXCEPTION 'trigger mismatch: admin_audit_log must have no triggers (append-only)';
    END IF;
END
$verify$;

-- ─────────────────────────────────────────────────────────────────────────
-- 사람이 눈으로 보는 자리. 위 단언이 통과한 뒤에만 여기까지 온다.
-- ─────────────────────────────────────────────────────────────────────────

-- 행위별 기록 수. `without_actor` 는 로그인 실패이므로 0이 아닌 것이 정상이다.
SELECT count(*) AS rows_total,
       count(*) FILTER (WHERE admin_user_id IS NULL) AS without_actor,
       count(DISTINCT action) AS actions,
       max(created_at) AS latest
FROM admin_audit_log;
