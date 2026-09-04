-- ---------------------------------------------------------------------
-- answer_reports : AI 답변 신고 (D-053 · 콘솔 로드맵 A1)
-- ---------------------------------------------------------------------
-- Order: … -> 03_auth -> 05_pets -> 07_chats -> 10_answer_reports
--
-- 앱에서 답변을 길게 누르면 지금은 메일 앱이 열려 운영 메일로 간다. 본문에 답변
-- 원문만 있고 **누가 · 언제 · 무슨 질문에 대한 답인지 · 무엇을 근거로 낸 답인지가
-- 없어서**, 받아도 무엇을 고쳐야 할지 알 수 없다. 이 표는 신고가 chat_turns 한 행을
-- 가리키게 해서 그 전부를 되찾는다.
--
-- ⚠️ **답변 원문을 여기 복사하지 않는다.** 원문은 chat_turns 한 곳이다 (D-048).
--    복사하면 탈퇴 시 명시 삭제 대상이 둘이 되고, 둘이 어긋나는 날이 온다.
--
-- ⚠️ **메일 경로는 그대로 남는다** (2026-09-03 사람 결정). 구버전 앱과 저장되지 않은
--    대화는 계속 메일로 오고, 콘솔은 이 표에 들어온 것만 본다.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS answer_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- **ON DELETE CASCADE 가 이 표의 핵심 배선이다** (D-053 "따라 나오는 것").
    -- 탈퇴 트랜잭션이 대화를 명시 삭제하므로(services/app_auth.py), 탈퇴한 회원의
    -- 신고는 **처리 중이더라도 함께 사라진다.** 원문 없이 신고를 판단할 수 없으니
    -- 그것이 맞다 — 남겨 두면 영영 못 여는 참조만 쌓인다.
    turn_id UUID NOT NULL REFERENCES chat_turns(id) ON DELETE CASCADE,

    -- 신고한 사람. app_users 행은 탈퇴해도 남으므로(status='withdrawn') 이 CASCADE 는
    -- 사실상 돌지 않는다 — 실제로 신고를 지우는 것은 위의 turn_id 쪽이다.
    -- chat_sessions 가 같은 모양이라 맞춰 둔다.
    app_user_id UUID NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,

    reason TEXT NOT NULL,

    status VARCHAR(20) NOT NULL DEFAULT 'open',

    -- 처리한 관리자. ON DELETE RESTRICT 는 admin_users 를 지우지 않고 suspended 로
    -- 막는다는 설계를 DB 가 지키게 하는 것이다 (admin_audit_log 와 같다).
    reviewed_by UUID REFERENCES admin_users(id) ON DELETE RESTRICT,
    reviewed_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- **같은 답변을 여러 사람이 신고하는 것은 막지 않는다** — 몇 번 신고됐는지가
    -- 그 답변이 얼마나 나쁜지의 신호다. 막는 것은 한 사람이 같은 답변을 여러 번
    -- 올리는 것뿐이다.
    CONSTRAINT answer_reports_turn_user_key UNIQUE (turn_id, app_user_id),

    CONSTRAINT answer_reports_status_check
        CHECK (status IN ('open', 'reviewed', 'dismissed')),

    CONSTRAINT answer_reports_reason_length_check
        CHECK (char_length(reason) BETWEEN 1 AND 500),

    -- 처리했으면 누가 · 언제가 반드시 있고, 안 했으면 반드시 없다. 한쪽만 채워진
    -- 행이 생기면 "누가 처리했나" 를 세는 것이 집계가 아니라 추측이 된다.
    CONSTRAINT answer_reports_review_state_check CHECK (
        (status = 'open' AND reviewed_by IS NULL AND reviewed_at IS NULL)
        OR
        (status <> 'open' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)
    )
);

-- 목록은 최근 순 키셋 페이지네이션이다 (admin_audit_log 와 같은 방식).
CREATE INDEX IF NOT EXISTS answer_reports_created_idx
    ON answer_reports (created_at DESC, id DESC);

-- 상태 필터가 걸린 목록. 부분 인덱스가 아니라 복합인 것은 세 상태를 다 쓰기 때문이다.
CREATE INDEX IF NOT EXISTS answer_reports_status_created_idx
    ON answer_reports (status, created_at DESC, id DESC);

-- "이 답변이 몇 번 신고됐나" 를 세는 자리. FK 쪽 인덱스도 겸한다.
CREATE INDEX IF NOT EXISTS answer_reports_turn_idx
    ON answer_reports (turn_id);
