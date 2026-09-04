-- =====================================================================
-- 2026-09-04_admin_audit_log.sql
-- 이미 돌고 있는 서버 DB 에 손으로 적용하는 사본 (db/init/03_auth.sql 과 동일)
-- 여러 번 돌려도 안전하다 (IF NOT EXISTS)
-- 적용: docker compose exec -T pgvector psql -U <user> -d vectordb < 이_파일
-- =====================================================================
--
-- admin_users 를 FK 로 참조하므로 03_auth 뒤에 온다.
--
-- 관리자가 무엇을 했는지 남기는 기록이다. **로그가 아니라 데이터다** — 운영
-- 로그(에러 · 스택트레이스)는 파일로 가고 여기 넣지 않는다 (docs/console/roadmap.md §6).
--
-- 지금 이것부터 만드는 이유는 뒤에 올 화면 셋(관리자 계정 발급 · 회원 조회와
-- 개인정보 복호화 · AI 답변 신고 처리)이 전부 "누가 했나"를 남겨야 하는 일이라,
-- 나중에 넣으면 그 세 카드를 다시 열어야 하기 때문이다.
--
-- **두 DB 에 각각 적용해야 한다.** 로컬 서버(개발 DB)는 이 PR 이 dev 에 머지된 뒤,
-- GCP(운영 DB)는 다음 dev→main 배포 때 밀려 있는 마이그레이션과 함께 적용한다
-- (docs/deploy/roadmap.md §2-5).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin_audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- **NULL 이 허용되는 이유는 로그인 실패 때문이다** — 없는 아이디로 두드린
    -- 시도는 가리킬 admin_users 행이 아예 없다. 무엇을 시도했는지는 detail 의
    -- login_id 에 남는다.
    --
    -- ON DELETE RESTRICT 는 admin_users 를 지우지 않고 suspended 로 막는다는
    -- 기존 설계를 DB 가 지키게 하는 것이다. refresh_tokens 의 CASCADE 와 반대인
    -- 것은 의도한 차이다 — 세션은 없어져야 하고 기록은 남아야 한다.
    admin_user_id UUID REFERENCES admin_users(id) ON DELETE RESTRICT,

    -- 점으로 구분한 소문자 ('admin.login.success').
    -- role · status 와 달리 CHECK 로 묶지 않는다 — 카드마다 늘어나는 목록이라
    -- 묶으면 화면이 하나 생길 때마다 두 DB 에 ALTER 를 돌려야 한다.
    action VARCHAR(60) NOT NULL,

    -- 대상이 없는 행위(로그인)는 둘 다 NULL 이다.
    -- FK 를 걸지 않는 이유는 가리키는 테이블이 target_type 에 따라 달라져서다.
    target_type VARCHAR(30),
    target_id UUID,

    -- **복호화된 개인정보를 넣지 않는다** — "무엇을 열었나"까지다.
    detail JSONB,

    request_id VARCHAR(64),
    ip INET,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT admin_audit_log_detail_object_check
        CHECK (detail IS NULL OR jsonb_typeof(detail) = 'object')
);

-- append-only 라 updated_at 도 트리거도 만들지 않는다.
-- 다른 테이블에 다 있는 것이 여기만 없는 것은 빠뜨린 게 아니다.

-- 감사 로그는 늘 최근순으로 본다. 로그인 시도까지 들어와서 행이 빨리 는다.
CREATE INDEX IF NOT EXISTS idx_admin_audit_log_created
    ON admin_audit_log (created_at DESC);

-- "이 관리자가 무엇을 했나". 주체가 없는 실패 행은 뺀다.
CREATE INDEX IF NOT EXISTS idx_admin_audit_log_admin
    ON admin_audit_log (admin_user_id, created_at DESC)
    WHERE admin_user_id IS NOT NULL;

-- "이 회원에게 무슨 일이 있었나". 대상이 없는 행(로그인)은 뺀다.
CREATE INDEX IF NOT EXISTS idx_admin_audit_log_target
    ON admin_audit_log (target_type, target_id)
    WHERE target_id IS NOT NULL;

COMMENT ON TABLE  admin_audit_log               IS '관리자 행위 감사 기록 (append-only) / 운영 로그는 파일에 따로';

COMMENT ON COLUMN admin_audit_log.id            IS '감사 행 고유 ID';
COMMENT ON COLUMN admin_audit_log.admin_user_id IS '행위 주체 / 로그인 실패는 NULL (가리킬 계정이 없음). 삭제는 RESTRICT';
COMMENT ON COLUMN admin_audit_log.action        IS '무엇을 했나 (admin.login.success 등) / 목록은 models/admin_audit_log.py';
COMMENT ON COLUMN admin_audit_log.target_type   IS '대상 종류 (app_user / admin_user 등) / 대상 없는 행위는 NULL';
COMMENT ON COLUMN admin_audit_log.target_id     IS '대상 id / FK 없음 (테이블이 target_type 에 따라 다름)';
COMMENT ON COLUMN admin_audit_log.detail        IS '부속 정보 JSONB (객체만) / 복호화된 개인정보 금지';
COMMENT ON COLUMN admin_audit_log.request_id    IS '요청 식별자 (로그 · 지표와 이어 붙이는 값)';
COMMENT ON COLUMN admin_audit_log.ip            IS '행위 당시 IP (nginx X-Real-IP)';
COMMENT ON COLUMN admin_audit_log.created_at    IS '기록 시각 / 갱신하지 않으므로 updated_at 없음';
