-- =====================================================================
-- 07_chats.sql
-- 대화 기록(세션 · 메시지)과 저장된 AI 대화 요약
-- 실행 순서: 01_schema -> 02_trigger -> 03_auth -> 04_crawl_runs -> 05_pets -> 06_walks -> 07_chats
-- =====================================================================
--
-- app_users 와 pets 를 FK 로 참조하므로 05_pets 뒤에 온다.
--
-- **왜 서버에 두나.** `POST /assistant/query` 는 지금까지 완전히 무상태였다.
-- 답이 응답으로만 나가고 어디에도 안 남아서, 앱을 닫으면 어제 받은 답을 다시
-- 꺼낼 방법이 없다. 능력 라우팅 결과(training · life · walk)도 마찬가지로
-- 응답에만 있어서 "그 답이 어느 능력에서 나왔는지"를 나중에 되짚을 수 없다.

-- ---------------------------------------------------------------------
-- chat_sessions : 대화 한 묶음. **사용자+강아지마다 5개까지만 남는다.**
-- ---------------------------------------------------------------------
-- 상한은 DB 가 아니라 services/chat.py 가 지킨다. CHECK 로는 "행 5개"를 셀 수
-- 없고, 트리거로 지우면 삭제가 조용히 일어나 앱이 이유를 못 본다.
--
-- **스코프가 (app_user_id, pet_id) 인 이유**는 강아지마다 대화가 갈리기 때문이다.
-- 두 마리를 키우면 훈련 이야기의 대상이 다르고, 한 아이의 대화가 다른 아이의
-- 대화를 밀어내면 사용자는 지운 적이 없는 기록이 사라진 것으로 본다.
CREATE TABLE IF NOT EXISTS chat_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- 계정이 지워지면 대화도 같이 지운다. walks · pets 와 같은 이유다 —
    -- 탈퇴가 개인정보를 파기하는데 대화가 남으면 파기가 반쪽이다.
    -- 질문 원문에는 생활 반경과 건강 사정이 그대로 들어 있다.
    app_user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,

    -- 강아지를 지우면 그 아이의 대화도 지운다. 대화의 주어가 사라진 기록은
    -- 목록에서 "누구 이야기인지 모르는 카드"가 된다.
    pet_id UUID NOT NULL REFERENCES pets(id) ON DELETE CASCADE,

    -- 목록 카드에 뜨는 제목. 첫 사용자 메시지에서 서버가 잘라 만든다.
    -- **LLM 을 부르지 않는다** — 카드 제목 때문에 대화마다 생성 비용을 물 이유가
    -- 없고, 요약은 사용자가 누를 때만 만든다는 것이 이 카드의 전제다.
    title VARCHAR(120) NOT NULL,

    -- 이 세션에 실제로 관여한 능력들. 라우팅이 낸 이름을 그대로 담는다
    -- (training · life · walk — orchestration/contracts.py CapabilityName).
    -- **한글 배지 라벨로 접어서 저장하지 않는다.** 라벨은 앱의 어휘이고,
    -- 여기에 한글을 넣으면 라벨을 바꿀 때 이미 쌓인 행을 전부 고쳐야 한다.
    -- 메시지마다의 값은 chat_messages.agent_categories 에 있고 여기는 그 합집합이다.
    agent_categories TEXT[] NOT NULL DEFAULT '{}',

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- 목록 정렬 기준이자 5개 유지에서 **가장 오래된 것**을 고르는 기준이다.
    -- 메시지가 붙을 때마다 services 가 올린다.
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 목록 조회(내 것 + 이 강아지 + 최근 순)와 5개 유지 판정이 같은 인덱스를 쓴다.
CREATE INDEX IF NOT EXISTS chat_sessions_scope_idx
    ON chat_sessions (app_user_id, pet_id, updated_at DESC);

-- ---------------------------------------------------------------------
-- chat_messages : 세션 안의 말 한 마디
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chat_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- **세션이 사라지면 메시지도 사라진다.** 5개 유지로 밀려난 세션의 원문이
    -- 남아 있으면 "지웠다"가 거짓말이 된다. 저장된 요약은 별도 테이블이라 남는다.
    session_id UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,

    role VARCHAR(10) NOT NULL,
    CONSTRAINT chat_messages_role_check CHECK (role IN ('user', 'assistant')),

    content TEXT NOT NULL,

    -- 이 답을 만든 능력들. 사용자 메시지는 빈 배열이다.
    -- 라우팅 메타데이터가 원천이고 클라이언트가 보낸 값을 믿지 않는다.
    agent_categories TEXT[] NOT NULL DEFAULT '{}',

    -- 오케스트레이션 최상위 상태(ANSWERED · PARTIAL · UNCERTAIN · REFUSED …).
    -- **실패한 답을 완료된 답처럼 저장하지 않기 위해** 같이 남긴다.
    assistant_status VARCHAR(20),

    -- 오케스트레이션이 발급한 request_id. 로그와 대조할 때 쓴다.
    request_id VARCHAR(64),

    -- 기기가 만든 멱등 키. 같은 탭을 두 번 눌렀거나 네트워크 재시도가 일어나도
    -- 같은 키면 한 번만 들어간다. walks.client_session_id 와 같은 방식이다.
    client_message_id VARCHAR(64),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS chat_messages_session_idx
    ON chat_messages (session_id, created_at, id);

-- 멱등 키는 **세션 안에서만** 유일하다. 기기가 세션마다 1부터 세도 안전하다.
-- NULL 은 여러 개 허용된다 — 키를 안 보내는 호출(관리 도구 등)을 막지 않는다.
CREATE UNIQUE INDEX IF NOT EXISTS chat_messages_idempotency_idx
    ON chat_messages (session_id, client_message_id)
    WHERE client_message_id IS NOT NULL;

-- ---------------------------------------------------------------------
-- chat_summaries : 사용자가 눌러서 저장한 AI 대화 요약 (보관함)
-- ---------------------------------------------------------------------
-- **원문 대화와 별개의 기록이다.** 5개 유지가 원본 세션을 밀어내도 요약은 남는다.
-- 그것이 session_id 가 NULL 을 허용하고 ON DELETE SET NULL 인 이유다 —
-- CASCADE 로 두면 사용자가 "저장" 을 눌러 보관한 것이 조용히 사라진다.
CREATE TABLE IF NOT EXISTS chat_summaries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- 소유자는 세션이 아니라 계정이다. 세션이 사라져도 주인은 남아야 조회된다.
    app_user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    pet_id UUID NOT NULL REFERENCES pets(id) ON DELETE CASCADE,

    -- 원본이 사라지면 NULL 이 된다. 앱은 이 값이 비었으면 "원본 대화 없음"으로
    -- 보여 주고 이어서 열기 버튼을 감춘다.
    session_id UUID REFERENCES chat_sessions(id) ON DELETE SET NULL,

    -- 구조화해서 담는다. 자유 서술 한 덩어리로 두면 앱이 화면을 못 짜고,
    -- 모델이 형식을 바꿔도 아무도 모른다.
    title VARCHAR(120) NOT NULL,
    question_summary TEXT NOT NULL,
    answer_summary TEXT NOT NULL,

    -- **원문의 주의·한계·출처를 접지 않는다.** 요약이 경고를 떨어뜨리면
    -- 보관함에 남는 것은 원문보다 위험한 문장이 된다.
    key_points TEXT[] NOT NULL DEFAULT '{}',
    cautions TEXT[] NOT NULL DEFAULT '{}',
    source_citations TEXT[] NOT NULL DEFAULT '{}',

    agent_categories TEXT[] NOT NULL DEFAULT '{}',

    -- 무엇으로 만들었는지. 프롬프트를 고쳤을 때 옛 요약과 새 요약을 가를 수 있어야 한다.
    model VARCHAR(60) NOT NULL,
    prompt_version VARCHAR(60) NOT NULL,

    -- 무엇을 요약했는지. 대화가 이어진 뒤 다시 요약하면 이 숫자가 달라진다.
    source_message_count INTEGER NOT NULL,

    -- 기기가 만든 멱등 키. 요약 버튼을 두 번 눌러도 한 건만 남는다.
    -- 생성이 유료 호출이라 메시지보다 이쪽이 더 중요하다.
    client_request_id VARCHAR(64) NOT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 멱등 키는 계정 안에서 유일하다. 세션이 NULL 이 될 수 있어 세션 기준으로는 못 건다.
CREATE UNIQUE INDEX IF NOT EXISTS chat_summaries_idempotency_idx
    ON chat_summaries (app_user_id, client_request_id);

-- 보관함 목록: 내 것 + 이 강아지, 최근 순.
CREATE INDEX IF NOT EXISTS chat_summaries_scope_idx
    ON chat_summaries (app_user_id, pet_id, created_at DESC);
