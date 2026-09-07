-- Read-only catalog assertions. Run with psql -X -v ON_ERROR_STOP=1.
-- Uses the connection's search_path, including disposable schemas in tests.
--
-- **단언형이다** — 이유는 verify_2026-09-05_app_user_nickname.sql 머리말과 같다.
-- 사람이 눈으로 볼 질의는 단언 뒤에 남겼다.
-- (2026-09-07 #295 에 단언형으로 다시 썼다. 그 전에는 SELECT 나열이라 **틀려도 녹색**이었다.)
--
-- 표 셋. 이 장에서 지켜야 할 것은 컬럼 목록이 아니라 **상태 기계**다 —
-- `chat_turns`·`chat_summaries` 의 `_state_check` 가 "처리 중 / 끝남 / 실패" 세 모양 중
-- 하나만 되게 묶는다. 그것이 없으면 **반쯤 채워진 행**(답은 있는데 상태는 processing 이거나,
-- 실패인데 error_code 가 빈)이 들어오고, 화면은 그것을 그냥 그려 버린다.
DO $verify$
DECLARE
    item record;
    sessions regclass;
    turns regclass;
    summaries regclass;
    definition text;
BEGIN
    FOR item IN SELECT * FROM (VALUES
        ('chat_sessions'), ('chat_turns'), ('chat_summaries')
    ) AS expected(table_name) LOOP
        IF to_regclass(item.table_name) IS NULL THEN
            RAISE EXCEPTION 'missing table: %', item.table_name;
        END IF;
    END LOOP;
    sessions := to_regclass('chat_sessions');
    turns := to_regclass('chat_turns');
    summaries := to_regclass('chat_summaries');

    -- ── 컬럼 ────────────────────────────────────────────────────────────
    FOR item IN SELECT * FROM (VALUES
        ('chat_sessions', 'app_user_id', 'uuid', 'true'),
        -- **대화는 강아지에 매인다** — 어느 아이 이야기인지가 답을 가른다 (B4 의 `dog` 컨텍스트).
        ('chat_sessions', 'pet_id', 'uuid', 'true'),
        ('chat_sessions', 'title', 'character varying(120)', 'true'),
        ('chat_sessions', 'agent_categories', 'text[]', 'true'),
        -- **NULL 이 "아직 한 마디도 안 한 방"** 이다. 아래 부분 인덱스 둘이 그 뜻에 걸린다.
        ('chat_sessions', 'last_message_at', 'timestamp with time zone', 'false'),
        ('chat_turns', 'session_id', 'uuid', 'true'),
        -- 앱이 만든 id. 재전송이 한 줄이 되게 하는 값이라 UNIQUE 의 짝이다.
        ('chat_turns', 'client_message_id', 'uuid', 'true'),
        ('chat_turns', 'processing_status', 'character varying(20)', 'true'),
        ('chat_turns', 'user_content', 'text', 'true'),
        ('chat_turns', 'assistant_content', 'text', 'false'),
        ('chat_turns', 'assistant_status', 'character varying(20)', 'false'),
        ('chat_turns', 'public_response', 'jsonb', 'false'),
        ('chat_turns', 'error_code', 'character varying(64)', 'false'),
        ('chat_summaries', 'app_user_id', 'uuid', 'true'),
        ('chat_summaries', 'pet_id', 'uuid', 'true'),
        -- **원본 방이 사라져도 요약은 남는다** — 아래 FK 의 SET NULL 과 짝이다.
        ('chat_summaries', 'source_session_id', 'uuid', 'false'),
        ('chat_summaries', 'source_turn_count', 'integer', 'true'),
        ('chat_summaries', 'client_request_id', 'uuid', 'true'),
        ('chat_summaries', 'processing_status', 'character varying(20)', 'true'),
        ('chat_summaries', 'source_citations', 'jsonb', 'false')
    ) AS expected(table_name, column_name, type_name, required) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_attribute a
            WHERE a.attrelid = to_regclass(item.table_name) AND a.attname = item.column_name
              AND a.attnum > 0 AND NOT a.attisdropped
              AND format_type(a.atttypid, a.atttypmod) = item.type_name
              AND a.attnotnull = item.required::boolean
        ) THEN
            RAISE EXCEPTION 'column mismatch: %.% (type %, not null %)',
                item.table_name, item.column_name, item.type_name, item.required;
        END IF;
    END LOOP;

    -- ── FK · UNIQUE ─────────────────────────────────────────────────────
    -- **삭제 동작이 한 곳만 다르다.** 회원·강아지는 CASCADE(탈퇴가 대화를 파기한다)인데
    -- `source_session_id` 만 **SET NULL** 이다 — 방을 지워도 요약은 남아야 하기 때문이다.
    -- CASCADE 로 뒤바뀌면 방 하나 지울 때 그 방에서 뽑은 요약이 조용히 같이 사라진다.
    FOR item IN SELECT * FROM (VALUES
        ('chat_sessions', 'f', 'app_user_id', 'app_users', 'c'),
        ('chat_sessions', 'f', 'pet_id', 'pets', 'c'),
        ('chat_turns', 'f', 'session_id', 'chat_sessions', 'c'),
        -- 같은 방에 같은 client_message_id 는 한 줄. 재전송이 대화를 두 배로 만들지 않는다.
        ('chat_turns', 'u', 'session_id,client_message_id', NULL, NULL),
        ('chat_summaries', 'f', 'app_user_id', 'app_users', 'c'),
        ('chat_summaries', 'f', 'pet_id', 'pets', 'c'),
        ('chat_summaries', 'f', 'source_session_id', 'chat_sessions', 'n'),
        ('chat_summaries', 'u', 'app_user_id,client_request_id', NULL, NULL)
    ) AS expected(table_name, kind, columns, target_table, delete_action) LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint c
            WHERE c.conrelid = to_regclass(item.table_name) AND c.contype::text = item.kind
              AND c.convalidated
              AND ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(num, pos)
                        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.num
                        ORDER BY k.pos) = string_to_array(item.columns, ',')
              AND (item.kind <> 'f' OR (
                  c.confrelid = to_regclass(item.target_table)
                  AND c.confdeltype::text = item.delete_action
              ))
        ) THEN
            RAISE EXCEPTION 'constraint mismatch: % kind % columns % (delete %)',
                item.table_name, item.kind, item.columns, item.delete_action;
        END IF;
    END LOOP;

    -- ── 상태 기계 ───────────────────────────────────────────────────────
    -- **이 파일의 핵심이다.** 세 모양 중 하나만 되게 묶는 CHECK 이고, 없으면 반쯤 채워진
    -- 행이 들어온다 — 타입도 NOT NULL 도 그대로라 위의 컬럼 단언은 전부 통과한다.
    FOR item IN SELECT * FROM (VALUES
        ('chat_turns', 'chat_turns_state_check', 'processing'),
        ('chat_turns', 'chat_turns_processing_status_check', 'failed'),
        ('chat_turns', 'chat_turns_assistant_status_check', 'REFUSED'),
        ('chat_turns', 'chat_turns_user_content_length_check', '2000'),
        ('chat_turns', 'chat_turns_assistant_content_length_check', '8000'),
        ('chat_summaries', 'chat_summaries_state_check', 'completed'),
        ('chat_summaries', 'chat_summaries_processing_status_check', 'failed'),
        -- 요약이 삼키는 턴 수의 상한. 없으면 방 하나를 통째로 넣는 요청이 통과한다.
        ('chat_summaries', 'chat_summaries_source_turn_count_check', '30')
    ) AS expected(table_name, conname, fragment) LOOP
        SELECT pg_get_constraintdef(c.oid) INTO definition
        FROM pg_constraint c
        WHERE c.conrelid = to_regclass(item.table_name) AND c.conname = item.conname
          AND c.contype = 'c' AND c.convalidated;
        IF definition IS NULL THEN
            RAISE EXCEPTION 'constraint mismatch: % missing or not validated', item.conname;
        END IF;
        -- 정의를 통째로 비교하지 않는다 (README 의 함정 ①) — 값 하나가 살아 있는지만 본다.
        IF position(item.fragment IN definition) = 0 THEN
            RAISE EXCEPTION 'constraint mismatch: % lost %, got %',
                item.conname, item.fragment, definition;
        END IF;
    END LOOP;

    -- `REFUSED` 만이 아니라 어시스턴트 상태 여덟이 다 있어야 한다. 하나가 빠지면 그 결말의
    -- 턴을 아예 못 적는다 — 오케스트레이션의 `CapabilityResult` 축과 맞물린 값들이다.
    SELECT pg_get_constraintdef(c.oid) INTO definition
    FROM pg_constraint c
    WHERE c.conrelid = turns AND c.conname = 'chat_turns_assistant_status_check';
    FOR item IN SELECT * FROM (VALUES
        ('ANSWERED'), ('PARTIAL'), ('CLARIFY'), ('HANDOFF'),
        ('UNCERTAIN'), ('REFUSED'), ('PENDING'), ('FAILED')
    ) AS expected(value) LOOP
        IF position('''' || item.value || '''' IN definition) = 0 THEN
            RAISE EXCEPTION 'constraint mismatch: assistant_status lost %, got %',
                item.value, definition;
        END IF;
    END LOOP;

    -- ── 인덱스 ──────────────────────────────────────────────────────────
    -- **부분 인덱스 둘이 `last_message_at IS NULL` 의 뜻을 집행한다.**
    --   `one_draft`  : 아직 한 마디도 안 한 방은 사람·강아지마다 **하나뿐**이다.
    --                  UNIQUE 나 WHERE 가 빠지면 빈 방이 무한히 쌓인다.
    --   `active_order`: 목록은 말한 방만, 최근순.
    FOR item IN SELECT * FROM (VALUES
        ('chat_sessions', 'chat_sessions_one_draft_idx', 'true', 'last_message_at IS NULL'),
        ('chat_sessions', 'chat_sessions_active_order_idx', 'false', 'last_message_at IS NOT NULL'),
        ('chat_turns', 'chat_turns_session_order_idx', 'false', NULL),
        ('chat_summaries', 'chat_summaries_scope_order_idx', 'false', NULL),
        -- 같은 방·같은 턴 수로 요약을 두 번 예약하지 못하게. 실패한 건은 빠진다.
        ('chat_summaries', 'chat_summaries_source_reservation_idx', 'true',
         'source_session_id IS NOT NULL')
    ) AS expected(table_name, index_name, unique_wanted, predicate) LOOP
        SELECT pg_get_indexdef(i.indexrelid) INTO definition
        FROM pg_index i
        JOIN pg_class c ON c.oid = i.indexrelid
        WHERE i.indrelid = to_regclass(item.table_name) AND c.relname = item.index_name
          AND i.indisvalid AND i.indisready
          AND i.indisunique = item.unique_wanted::boolean;
        IF definition IS NULL THEN
            RAISE EXCEPTION 'index mismatch: % missing, invalid, or unique flag is not %',
                item.index_name, item.unique_wanted;
        END IF;
        IF item.predicate IS NOT NULL AND position(item.predicate IN definition) = 0 THEN
            RAISE EXCEPTION 'index mismatch: % lost its WHERE %, got %',
                item.index_name, item.predicate, definition;
        END IF;
    END LOOP;
END
$verify$;

-- ─────────────────────────────────────────────────────────────────────────
-- 사람이 눈으로 보는 자리. 위 단언이 통과한 뒤에만 여기까지 온다.
-- ─────────────────────────────────────────────────────────────────────────

-- 방·턴·요약 수와, 아직 한 마디도 안 한 방.
SELECT (SELECT count(*) FROM chat_sessions) AS sessions,
       (SELECT count(*) FROM chat_sessions WHERE last_message_at IS NULL) AS drafts,
       (SELECT count(*) FROM chat_turns) AS turns,
       (SELECT count(*) FROM chat_summaries) AS summaries;

-- 처리 상태 분포. `processing` 이 오래 남아 있으면 워커가 죽은 것이다.
SELECT processing_status, count(*) AS turns, max(processing_started_at) AS latest
FROM chat_turns
GROUP BY processing_status
ORDER BY turns DESC;
