-- 2026-09-01_chats.sql
-- 대화 기록(세션 · 메시지)과 저장된 AI 대화 요약을 만든다.
-- db/init/07_chats.sql 과 같은 결과가 되게 한다.
--
-- **여러 번 돌려도 안전하다.** 이 저장소는 버전 테이블이 없어서 DB 가 적용 여부를
-- 기억하지 않는다 — db/migrations/README.md 참고.
--
-- 왜: `POST /assistant/query` 가 무상태라 대화가 어디에도 안 남는다. 앱을 닫으면
-- 어제 받은 답을 다시 꺼낼 방법이 없고, 라우팅이 낸 능력(training · life · walk)도
-- 응답에만 있어서 나중에 되짚을 수 없다.
--
-- **새로 만들기만 한다.** 기존 테이블을 고치거나 지우지 않으므로 이미 쌓인 행에
-- 영향이 없다.

BEGIN;

CREATE TABLE IF NOT EXISTS chat_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    app_user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    pet_id UUID NOT NULL REFERENCES pets(id) ON DELETE CASCADE,
    title VARCHAR(120) NOT NULL,

    -- 라우팅이 낸 능력 이름 그대로 (training · life · walk). 한글 배지 라벨로
    -- 접어서 저장하지 않는다 — 라벨은 앱의 어휘다.
    agent_categories TEXT[] NOT NULL DEFAULT '{}',

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS chat_sessions_scope_idx
    ON chat_sessions (app_user_id, pet_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS chat_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- 세션이 사라지면 원문도 사라진다. 5개 유지로 밀려난 대화의 원문이 남아
    -- 있으면 "지웠다"가 거짓말이 된다.
    session_id UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,

    role VARCHAR(10) NOT NULL,
    content TEXT NOT NULL,
    agent_categories TEXT[] NOT NULL DEFAULT '{}',
    assistant_status VARCHAR(20),
    request_id VARCHAR(64),
    client_message_id VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- CHECK 는 이름으로 존재를 확인해야 재실행이 안전하다. ADD CONSTRAINT 에는
-- IF NOT EXISTS 가 없다.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chat_messages_role_check'
    ) THEN
        ALTER TABLE chat_messages
            ADD CONSTRAINT chat_messages_role_check CHECK (role IN ('user', 'assistant'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS chat_messages_session_idx
    ON chat_messages (session_id, created_at, id);

CREATE UNIQUE INDEX IF NOT EXISTS chat_messages_idempotency_idx
    ON chat_messages (session_id, client_message_id)
    WHERE client_message_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS chat_summaries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    app_user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    pet_id UUID NOT NULL REFERENCES pets(id) ON DELETE CASCADE,

    -- **SET NULL 이지 CASCADE 가 아니다.** 5개 유지가 원본을 밀어내도 사용자가
    -- 저장해 둔 요약은 남아야 한다.
    session_id UUID REFERENCES chat_sessions(id) ON DELETE SET NULL,

    title VARCHAR(120) NOT NULL,
    question_summary TEXT NOT NULL,
    answer_summary TEXT NOT NULL,
    key_points TEXT[] NOT NULL DEFAULT '{}',
    cautions TEXT[] NOT NULL DEFAULT '{}',
    source_citations TEXT[] NOT NULL DEFAULT '{}',
    agent_categories TEXT[] NOT NULL DEFAULT '{}',
    model VARCHAR(60) NOT NULL,
    prompt_version VARCHAR(60) NOT NULL,
    source_message_count INTEGER NOT NULL,
    client_request_id VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS chat_summaries_idempotency_idx
    ON chat_summaries (app_user_id, client_request_id);

CREATE INDEX IF NOT EXISTS chat_summaries_scope_idx
    ON chat_summaries (app_user_id, pet_id, created_at DESC);

COMMIT;
