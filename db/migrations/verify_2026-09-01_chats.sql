-- verify_2026-09-01_chats.sql
-- 마이그레이션이 제대로 돌았는지 눈으로 본다. 아무것도 바꾸지 않는다.
--
--   docker compose exec -T pgvector psql -U <앱계정> -d vectordb -f - \
--       < db/migrations/verify_2026-09-01_chats.sql

-- 1) 세 테이블이 생겼는가. 세 줄이어야 한다.
SELECT table_name
FROM information_schema.tables
WHERE table_name IN ('chat_sessions', 'chat_messages', 'chat_summaries')
ORDER BY table_name;

-- 2) **가장 중요한 확인** — 원본이 사라져도 요약이 남는가.
--    chat_messages.session_id 는 CASCADE, chat_summaries.session_id 는 SET NULL
--    이어야 한다. 여기가 뒤바뀌면 사용자가 저장한 요약이 5개 유지에 조용히 지워진다.
SELECT
    tc.table_name,
    kcu.column_name,
    rc.delete_rule
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
    ON tc.constraint_name = kcu.constraint_name
JOIN information_schema.referential_constraints rc
    ON tc.constraint_name = rc.constraint_name
WHERE tc.constraint_type = 'FOREIGN KEY'
  AND tc.table_name IN ('chat_messages', 'chat_summaries')
  AND kcu.column_name = 'session_id'
ORDER BY tc.table_name;

-- 3) 멱등 인덱스가 걸렸는가. 두 줄이어야 한다 —
--    chat_messages_idempotency_idx, chat_summaries_idempotency_idx.
SELECT indexname
FROM pg_indexes
WHERE indexname LIKE 'chat_%_idempotency_idx'
ORDER BY indexname;

-- 4) 목록 조회용 인덱스가 걸렸는가. 세 줄이어야 한다.
SELECT indexname
FROM pg_indexes
WHERE tablename IN ('chat_sessions', 'chat_messages', 'chat_summaries')
  AND indexname LIKE '%_idx'
  AND indexname NOT LIKE '%idempotency%'
ORDER BY indexname;

-- 5) role CHECK 이 걸렸는가. 한 줄이어야 한다.
SELECT conname
FROM pg_constraint
WHERE conname = 'chat_messages_role_check';
