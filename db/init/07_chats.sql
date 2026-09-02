-- Product chat persistence (D-043). Raw content remains forbidden in logs/traces (D-037).
-- Order: 01_schema -> 02_trigger -> 03_auth -> 04_crawl_runs -> 05_pets -> 06_walks -> 07_chats

CREATE TABLE IF NOT EXISTS chat_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    app_user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    pet_id UUID NOT NULL REFERENCES pets(id) ON DELETE CASCADE,
    title VARCHAR(120) NOT NULL,
    agent_categories TEXT[] NOT NULL DEFAULT '{}',
    -- NULL is the single inactive draft; non-NULL means the session is active.
    last_message_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS chat_sessions_one_draft_idx
    ON chat_sessions (app_user_id, pet_id)
    WHERE last_message_at IS NULL;

CREATE INDEX IF NOT EXISTS chat_sessions_app_user_idx ON chat_sessions (app_user_id);
CREATE INDEX IF NOT EXISTS chat_sessions_pet_idx ON chat_sessions (pet_id);

CREATE INDEX IF NOT EXISTS chat_sessions_active_order_idx
    ON chat_sessions (app_user_id, pet_id, last_message_at DESC, id DESC)
    WHERE last_message_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS chat_turns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    client_message_id UUID NOT NULL,
    processing_status VARCHAR(20) NOT NULL,
    user_content TEXT NOT NULL,
    assistant_content TEXT,
    assistant_status VARCHAR(20),
    request_id VARCHAR(64),
    agent_categories TEXT[] NOT NULL DEFAULT '{}',
    -- Only the already-public AssistantResponse contract is stored here; never exceptions,
    -- prompts, credentials, or provider payloads.
    public_response JSONB,
    error_code VARCHAR(64),
    processing_started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chat_turns_session_client_key UNIQUE (session_id, client_message_id),
    CONSTRAINT chat_turns_processing_status_check
        CHECK (processing_status IN ('processing', 'completed', 'failed')),
    CONSTRAINT chat_turns_user_content_length_check
        CHECK (char_length(user_content) BETWEEN 1 AND 2000),
    CONSTRAINT chat_turns_assistant_content_length_check
        CHECK (assistant_content IS NULL OR char_length(assistant_content) BETWEEN 1 AND 8000),
    CONSTRAINT chat_turns_assistant_status_check
        CHECK (assistant_status IS NULL OR assistant_status IN (
            'ANSWERED', 'PARTIAL', 'CLARIFY', 'HANDOFF',
            'UNCERTAIN', 'REFUSED', 'PENDING', 'FAILED'
        )),
    CONSTRAINT chat_turns_state_check CHECK (
        (processing_status = 'processing'
            AND assistant_content IS NULL AND assistant_status IS NULL
            AND public_response IS NULL AND error_code IS NULL AND completed_at IS NULL)
        OR
        (processing_status = 'completed'
            AND assistant_content IS NOT NULL AND assistant_status IS NOT NULL
            AND request_id IS NOT NULL AND public_response IS NOT NULL
            AND jsonb_typeof(public_response) = 'object'
            AND error_code IS NULL AND completed_at IS NOT NULL)
        OR
        (processing_status = 'failed'
            AND assistant_content IS NULL AND assistant_status IS NULL
            AND public_response IS NULL AND error_code IS NOT NULL AND completed_at IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS chat_turns_session_order_idx
    ON chat_turns (session_id, created_at, id);

CREATE TABLE IF NOT EXISTS chat_summaries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    app_user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    pet_id UUID NOT NULL REFERENCES pets(id) ON DELETE CASCADE,
    source_session_id UUID REFERENCES chat_sessions(id) ON DELETE SET NULL,
    source_turn_count INTEGER NOT NULL,
    client_request_id UUID NOT NULL,
    processing_status VARCHAR(20) NOT NULL,

    -- Output is NULL while processing and after failure.
    title VARCHAR(120),
    question_summary TEXT,
    answer_summary TEXT,
    key_points TEXT[],
    cautions TEXT[],
    source_citations JSONB,
    agent_categories TEXT[] NOT NULL DEFAULT '{}',
    model VARCHAR(60),
    prompt_version VARCHAR(60),
    error_code VARCHAR(64),
    processing_started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chat_summaries_user_request_key UNIQUE (app_user_id, client_request_id),
    CONSTRAINT chat_summaries_processing_status_check
        CHECK (processing_status IN ('processing', 'completed', 'failed')),
    CONSTRAINT chat_summaries_source_turn_count_check
        CHECK (source_turn_count BETWEEN 1 AND 30),
    CONSTRAINT chat_summaries_state_check CHECK (
        (processing_status = 'processing'
            AND title IS NULL AND question_summary IS NULL AND answer_summary IS NULL
            AND key_points IS NULL AND cautions IS NULL AND source_citations IS NULL
            AND model IS NULL AND prompt_version IS NULL AND error_code IS NULL
            AND completed_at IS NULL)
        OR
        (processing_status = 'completed'
            AND title IS NOT NULL AND question_summary IS NOT NULL AND answer_summary IS NOT NULL
            AND key_points IS NOT NULL AND cautions IS NOT NULL AND source_citations IS NOT NULL
            AND jsonb_typeof(source_citations) = 'array'
            AND model IS NOT NULL AND prompt_version IS NOT NULL AND error_code IS NULL
            AND completed_at IS NOT NULL)
        OR
        (processing_status = 'failed'
            AND title IS NULL AND question_summary IS NULL AND answer_summary IS NULL
            AND key_points IS NULL AND cautions IS NULL AND source_citations IS NULL
            AND model IS NULL AND prompt_version IS NULL AND error_code IS NOT NULL
            AND completed_at IS NOT NULL)
    )
);

-- A failed attempt does not block a retry of the same source state.
CREATE UNIQUE INDEX IF NOT EXISTS chat_summaries_source_reservation_idx
    ON chat_summaries (source_session_id, source_turn_count)
    WHERE source_session_id IS NOT NULL
      AND processing_status IN ('processing', 'completed');

CREATE INDEX IF NOT EXISTS chat_summaries_scope_order_idx
    ON chat_summaries (app_user_id, pet_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS chat_summaries_pet_idx ON chat_summaries (pet_id);
CREATE INDEX IF NOT EXISTS chat_summaries_source_session_idx
    ON chat_summaries (source_session_id);
