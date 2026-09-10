-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — `tools/check_migration_verification.py` 가 스키마를 일부러 망가뜨린 뒤
-- 이 파일이 그것을 잡는지 본다 (CHECKS 의 `pet_invite_receipts` 항목). SELECT 만 있으면
-- 틀려도 통과하므로 사람이 볼 질의는 단언 뒤에 둔다.
--
-- ⚠ 실패 문구에 `mismatch` 나 `missing table` 이 들어가야 한다 — 하네스가 그 낱말로
--   "verifier 가 잡았다" 와 "변조 SQL 이 죽었다" 를 가른다.
DO $verify$
DECLARE
    item record;
    relation regclass;
BEGIN
    IF to_regclass('pet_invites') IS NULL THEN
        RAISE EXCEPTION 'missing table: pet_invites';
    END IF;
    relation := to_regclass('pet_invites');

    -- ① 영수증 두 칸. 둘 다 nullable — 아직 안 쓴 초대는 NULL 이다.
    FOR item IN SELECT * FROM (VALUES
        ('accepted_at', 'timestamp with time zone', 'false'),
        ('accepted_by', 'uuid',                     'false')
    ) AS expected(column_name, type_name, required) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = relation AND a.attname = item.column_name
              AND a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) = item.type_name
              AND a.attnotnull = item.required::boolean
        ) THEN
            RAISE EXCEPTION 'column mismatch: pet_invites.% (type %, not null %)',
                item.column_name, item.type_name, item.required;
        END IF;
    END LOOP;

    -- ② accepted_by 의 FK — app_users(id), ON DELETE SET NULL. 검증됐는지(convalidated)까지 본다.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        WHERE c.conrelid = relation AND c.contype = 'f'
          AND c.convalidated
          AND c.confrelid = to_regclass('app_users')
          AND c.confdeltype = 'n'  -- SET NULL
          AND ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(num, pos)
                    JOIN pg_attribute a ON a.attrelid = relation AND a.attnum = k.num
                    ORDER BY k.pos) = ARRAY['accepted_by']
    ) THEN
        RAISE EXCEPTION 'constraint mismatch: pet_invites accepted_by FK (expected SET NULL to app_users)';
    END IF;
END
$verify$;

-- 사람이 눈으로 볼 것 (단언이 다 통과한 뒤에만 여기 온다).
SELECT a.attname AS column_name, format_type(a.atttypid, a.atttypmod) AS type, a.attnotnull
FROM pg_attribute a
WHERE a.attrelid = to_regclass('pet_invites') AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY a.attnum;
