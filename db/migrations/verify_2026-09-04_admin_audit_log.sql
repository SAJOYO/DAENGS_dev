-- Read-only verification for 2026-09-04_admin_audit_log.sql.

-- 1) The table should exist.
SELECT table_name
FROM information_schema.tables
WHERE table_name = 'admin_audit_log';

-- 2) admin_user_id must be NULLABLE (login failures have no account to point at)
--    and every other column must keep its intended nullability.
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'admin_audit_log'
ORDER BY ordinal_position;

-- 3) The FK must be RESTRICT, not CASCADE -- accounts are suspended, never deleted,
--    and the audit trail must survive. (refresh_tokens is CASCADE on purpose.)
SELECT tc.table_name, kcu.column_name, rc.delete_rule
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON tc.constraint_name = kcu.constraint_name
JOIN information_schema.referential_constraints rc
  ON tc.constraint_name = rc.constraint_name
WHERE tc.constraint_type = 'FOREIGN KEY'
  AND tc.table_name = 'admin_audit_log';

-- 4) detail must be an object when present.
SELECT conname, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conname = 'admin_audit_log_detail_object_check';

-- 5) Three indexes, two of them partial.
SELECT indexname, indexdef
FROM pg_indexes
WHERE tablename = 'admin_audit_log'
ORDER BY indexname;

-- 6) append-only: there must be NO updated_at trigger on this table.
--    (Every other table in 03_auth.sql has one, so an empty result is the pass.)
SELECT tgname
FROM pg_trigger
WHERE tgrelid = 'admin_audit_log'::regclass
  AND NOT tgisinternal;
