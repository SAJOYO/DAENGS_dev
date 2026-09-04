-- =====================================================================
-- 2026-09-05_answer_reports.sql
-- 이미 돌고 있는 서버 DB 에 손으로 적용하는 사본 (db/init/10_answer_reports.sql 과 동일)
-- 여러 번 돌려도 안전하다 (IF NOT EXISTS)
-- 적용: docker compose exec -T pgvector psql -U <user> -d vectordb < 이_파일
-- =====================================================================
--
-- chat_turns · app_users · admin_users 를 FK 로 참조하므로 07_chats · 03_auth 뒤에 온다.
--
-- **두 DB 에 각각 적용해야 한다.** 로컬 서버(개발 DB)는 이 PR 이 dev 에 머지된 뒤,
-- GCP(운영 DB)는 다음 dev→main 배포 때 밀려 있는 마이그레이션과 함께 적용한다
-- (docs/deploy/roadmap.md §2-5 · docs/console/roadmap.md §2 "운영 DB 실측").
--
-- 적용 뒤 verify_2026-09-05_answer_reports.sql 도 돌린다.
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
