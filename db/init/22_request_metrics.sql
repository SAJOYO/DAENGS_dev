-- ---------------------------------------------------------------------
-- request_metrics : 요청 하나가 남기는 것 (D-037 · 콘솔 로드맵 B2)
-- ---------------------------------------------------------------------
-- Order: … -> 03_auth -> 07_chats -> 22_request_metrics
--
-- 운영 지표 화면(#223)이 제품 테이블(chat_*)만 센다. 그래서 "몇 건 물어봤나" 는
-- 보이는데 **"느렸나 · 왜 실패했나" 는 안 보인다.** 지연도 실패 사유도 어디에도
-- 안 쌓여서, 요청이 끝나면 그냥 사라진다. 이 표가 그 자리다.
--
-- ⚠️ **질문 원문을 여기 넣지 않는다.** D-037 이 오케스트레이션의 일반 관측에 질문
--    원문을 금지했고, 이 표의 열은 그 결정의 "허용" 칸을 그대로 옮긴 것이다.
--    원문이 필요한 진단은 D-054 의 트레이싱(옵트인 · 기본 꺼짐 · 보존 30일)이
--    따로 답한다. 둘은 겹치지 않는다 -- 이쪽은 집계, 저쪽은 한 건 파고들기다.
--
-- ⚠️ **회원 식별자(principal.subject)를 넣지 않는다.** 종류(ADMIN/APP_USER)만 남긴다.
--    누가 물었는지는 request_id -> chat_turns -> chat_sessions 로 우리 DB 안에 이미
--    있다. 관측용 표로 식별자를 복제해서 얻는 것이 0 인데 복제하면 그건 그냥 유출
--    표면이다 (D-054 가 트레이스에 그은 것과 같은 선).
--
-- **이 표는 제품 데이터가 아니다.** 지워도 서비스가 안 죽는다. 그래서 아무것도
-- 참조하지 않고 아무것도 이 표를 참조하지 않는다 -- chat_turns 를 FK 로 걸지 않는
-- 것은 일부러다:
--   ⓐ 대화로 안 남는 요청(라우터 실패 · 사교적 응답)도 여기에는 남아야 한다
--   ⓑ 탈퇴로 대화가 지워져도 지연 통계는 남아야 한다. CASCADE 로 같이 지워지면
--      "지난달이 느렸다" 가 사람이 탈퇴할 때마다 바뀐다
--
-- **append-only 다.** UPDATE 도 DELETE 도 없다 -- admin_audit_log 와 같은 이유로
-- updated_at 도 트리거도 없다. 다만 저쪽과 달리 **이건 지워도 되는 표다**
-- (보존 정책은 A5 와 별개로 정한다 -- 아래 마지막 주석).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS request_metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- 이 요청의 이름. **세 곳에서 같은 값이다** --
    -- 여기 · chat_turns.request_id · (트레이싱을 켜면) Cloud Trace 의 trace id.
    -- 마지막 것은 D-054 가 run_id 를 request_id 로 못박아서 그렇게 된다.
    --
    -- UNIQUE 를 걸지 않는다. 한 요청이 두 행을 남기는 것은 버그지만, 그 버그 때문에
    -- **답변이 500 이 되면 안 된다** -- 이 표의 쓰기는 사용자 응답을 막지 않는 것이
    -- 제일 중요한 성질이다 (routers/assistant.py). 중복은 집계에서 걸러 본다.
    request_id UUID NOT NULL,

    -- 누가 불렀나. **종류만이다.** contracts.py 의 PrincipalContext.kind 와 같은 값.
    principal_kind VARCHAR(20) NOT NULL,

    -- 목적지를 무엇이 골랐나. 'deterministic' 은 규칙, 'llm' 은 시맨틱 라우터다.
    -- 라우터가 아예 못 돈 요청(자격증명 차단 등)은 NULL 이다.
    router_kind VARCHAR(20),

    -- 실제로 실행된 능력들. 빈 배열이 정상이다 -- 사교적 응답과 라우터 실패는
    -- 아무 능력도 안 부른다. 그 둘을 "0개 능력" 으로 남기는 것이 이 표의 값이다.
    --
    -- 배열인 이유는 한 요청이 여럿을 부를 수 있어서다. 능력별 지연은 여기 없다 --
    -- 필요해지면 그때 자식 표를 만든다 (지금은 무엇이 느린지가 아니라
    -- 무엇이 느려지고 있는지를 본다).
    capabilities TEXT[] NOT NULL DEFAULT '{}',

    -- 결과. contracts.py 의 AssistantStatus 와 같은 값.
    status VARCHAR(30) NOT NULL,

    -- 거절 · 기권의 사유 코드. 정상 응답은 NULL 이다.
    -- **문구가 아니라 코드다** -- 문구는 사용자에게 보이는 말이라 바뀌고, 바뀌면
    -- 집계가 끊긴다.
    reason_code VARCHAR(60),

    -- 예외가 났으면 그 범주. 타입 이름 수준까지고 **메시지는 넣지 않는다** --
    -- 예외 메시지에는 질문이나 좌표가 섞여 들어온다.
    error_category VARCHAR(60),

    -- **이 표의 핵심.** 지금 이 값이 아무 데도 없다.
    elapsed_ms INTEGER NOT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- 음수 지연은 시계가 뒤로 갔다는 뜻이고, 그런 행이 섞이면 평균이 조용히 틀린다.
    CONSTRAINT request_metrics_elapsed_check CHECK (elapsed_ms >= 0),

    -- 종류는 둘뿐이다. action 과 달리 이건 카드마다 늘지 않으므로 CHECK 로 묶는다 --
    -- 오타로 'app_user' 가 섞이면 집계가 두 갈래로 갈리는데 아무 에러도 안 난다.
    CONSTRAINT request_metrics_principal_kind_check
        CHECK (principal_kind IN ('ADMIN', 'APP_USER')),

    CONSTRAINT request_metrics_router_kind_check
        CHECK (router_kind IS NULL OR router_kind IN ('deterministic', 'llm'))
);

-- 집계는 항상 기간으로 자른다 (최근 7 · 30 · 90일). 그래서 created_at 하나면 된다.
CREATE INDEX IF NOT EXISTS idx_request_metrics_created
    ON request_metrics (created_at DESC);

-- 신고 한 건에서 그 요청의 지연을 찾는 길. 콘솔의 신고 상세가 request_id 를 이미
-- 화면에 찍고 있어서(reports-console.tsx) 그 값으로 바로 온다.
CREATE INDEX IF NOT EXISTS idx_request_metrics_request_id
    ON request_metrics (request_id);

COMMENT ON TABLE request_metrics IS
    '요청 메타데이터 (D-037 허용 열만). 질문 원문·회원 식별자를 넣지 않는다';

-- ---------------------------------------------------------------------
-- 보존 -- **아직 안 정했다.** 감사 로그(A5)와 다른 표다:
--   · 감사 로그는 지우면 못 되돌린다. 여기는 지워도 된다 (제품 데이터가 아니다)
--   · 감사 로그는 하루 14행이다. 여기는 요청마다 한 행이라 훨씬 빨리 는다
-- 그래서 A5 의 "안 지운다" 를 이 표에 그대로 옮기지 말 것. 실제 증가량을 본 뒤
-- 별도로 정한다 (docs/console/roadmap.md §4 B2).
-- ---------------------------------------------------------------------
