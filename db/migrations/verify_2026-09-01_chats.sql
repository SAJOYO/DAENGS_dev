-- Read-only verification for the still-unapplied final chat schema.

-- 1) Exactly these three product tables should exist.
SELECT table_name
FROM information_schema.tables
WHERE table_name IN ('chat_sessions', 'chat_turns', 'chat_summaries', 'chat_messages')
ORDER BY table_name;

-- 2) Turn deletion is CASCADE; saved-summary source deletion is SET NULL.
SELECT tc.table_name, kcu.column_name, rc.delete_rule
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON tc.constraint_name = kcu.constraint_name
JOIN information_schema.referential_constraints rc
  ON tc.constraint_name = rc.constraint_name
WHERE tc.constraint_type = 'FOREIGN KEY'
  AND ((tc.table_name = 'chat_turns' AND kcu.column_name = 'session_id')
    OR (tc.table_name = 'chat_summaries' AND kcu.column_name = 'source_session_id'))
ORDER BY tc.table_name;

-- 3) Partial unique definitions must show one draft and retryable failed summaries.
SELECT indexname, indexdef
FROM pg_indexes
WHERE indexname IN (
    'chat_sessions_one_draft_idx',
    'chat_summaries_source_reservation_idx'
)
ORDER BY indexname;

-- 4) FK/order and idempotency indexes/constraints.
SELECT indexname, indexdef
FROM pg_indexes
WHERE tablename IN ('chat_sessions', 'chat_turns', 'chat_summaries')
ORDER BY tablename, indexname;

-- 5) State and length/count CHECKs.
SELECT conname, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conname IN (
    'chat_turns_processing_status_check',
    'chat_turns_user_content_length_check',
    'chat_turns_assistant_content_length_check',
    'chat_turns_assistant_status_check',
    'chat_turns_state_check',
    'chat_summaries_processing_status_check',
    'chat_summaries_source_turn_count_check',
    'chat_summaries_state_check'
)
ORDER BY conname;
