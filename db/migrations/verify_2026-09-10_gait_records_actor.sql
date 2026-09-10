-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — `tools/check_migration_verification.py` 가 스키마를 일부러 망가뜨린 뒤
-- 이 파일이 그것을 잡는지 본다 (CHECKS 의 `gait_records_actor` 항목). SELECT 만 있으면
-- 틀려도 통과하므로 사람이 볼 질의는 단언 뒤에 둔다.
--
-- ⚠ 실패 문구에 `mismatch` 나 `missing table` 이 들어가야 한다 — 하네스가 그 낱말로
--   "verifier 가 잡았다" 와 "변조 SQL 이 죽었다" 를 가른다.
DO $verify$
DECLARE
    relation regclass;
BEGIN
    IF to_regclass('gait_records') IS NULL THEN
        RAISE EXCEPTION 'missing table: gait_records';
    END IF;
    relation := to_regclass('gait_records');

    -- ① 컬럼: uuid, nullable — 옛 기록(이 칸이 생기기 전)은 NULL 이다.
    IF NOT EXISTS (
        SELECT 1 FROM pg_attribute a
        WHERE a.attrelid = relation AND a.attname = 'actor_app_user_id'
          AND a.attnum > 0 AND NOT a.attisdropped
          AND format_type(a.atttypid, a.atttypmod) = 'uuid'
          AND NOT a.attnotnull
    ) THEN
        RAISE EXCEPTION 'column mismatch: gait_records.actor_app_user_id (type uuid, nullable)';
    END IF;

    -- ② FK — app_users(id), **ON DELETE SET NULL**. 다른 delete-action(예: CASCADE)으로
    --   바뀌면 업로더가 탈퇴 뒤 재가입해도(app_users 행은 안 지워지지만, 언젠가 실제
    --   삭제가 도는 날) 기록 자체가 통째로 사라진다 — 이 칸은 소유권이 아니라 이력이라
    --   행이 남아야 한다(pet_invite_receipts 의 accepted_by 와 같은 이유).
    --   검증됐는지(convalidated)까지 본다 — NOT VALID 는 "있어도 없는 것".
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        WHERE c.conrelid = relation AND c.contype = 'f'
          AND c.convalidated
          AND c.confrelid = to_regclass('app_users')
          AND c.confdeltype = 'n'  -- SET NULL
          AND ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(num, pos)
                    JOIN pg_attribute a ON a.attrelid = relation AND a.attnum = k.num
                    ORDER BY k.pos) = ARRAY['actor_app_user_id']
    ) THEN
        RAISE EXCEPTION 'constraint mismatch: gait_records actor_app_user_id FK'
            ' (expected SET NULL to app_users)';
    END IF;
END
$verify$;

-- 사람이 눈으로 볼 것 (단언이 다 통과한 뒤에만 여기 온다).
SELECT a.attname AS column_name, format_type(a.atttypid, a.atttypmod) AS type, a.attnotnull
FROM pg_attribute a
WHERE a.attrelid = to_regclass('gait_records') AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY a.attnum;
