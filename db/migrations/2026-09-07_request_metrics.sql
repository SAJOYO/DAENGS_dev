-- 2026-09-07_request_metrics.sql
-- 요청 하나가 남기는 것을 담을 표를 만든다 (콘솔 로드맵 B2 · #297).
-- db/init/22_request_metrics.sql 과 같은 결과가 되게 한다.
--
-- **여러 번 돌려도 안전하다.** 이 저장소는 버전 테이블이 없어서 DB 가 적용 여부를
-- 기억하지 않는다 — db/migrations/README.md 참고.
--
-- 왜: 운영 지표 화면(#223)이 제품 테이블(chat_*)만 세서 "몇 건 물어봤나" 는 보이는데
-- "느렸나 · 왜 실패했나" 는 안 보인다. 지연도 실패 사유도 어디에도 안 쌓인다.
--
-- 저장처를 테이블로 정한 것은 2026-09-07 사람 결정이다 (로드맵 §7). 파일 로그 집계는
-- Hold 상태인 로그 카드(로깅 설정 · 회전)를 먼저 풀어야 해서 범위가 커진다.
--
-- ⚠ 질문 원문도 회원 식별자도 넣지 않는다 (D-037 · D-054). 열 목록이 곧 그 약속이다.

BEGIN;

CREATE TABLE IF NOT EXISTS request_metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id UUID NOT NULL,
    principal_kind VARCHAR(20) NOT NULL,
    router_kind VARCHAR(20),
    capabilities TEXT[] NOT NULL DEFAULT '{}',
    status VARCHAR(30) NOT NULL,
    reason_code VARCHAR(60),
    error_category VARCHAR(60),
    elapsed_ms INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT request_metrics_elapsed_check CHECK (elapsed_ms >= 0),
    CONSTRAINT request_metrics_principal_kind_check
        CHECK (principal_kind IN ('ADMIN', 'APP_USER')),
    CONSTRAINT request_metrics_router_kind_check
        CHECK (router_kind IS NULL OR router_kind IN ('deterministic', 'llm'))
);

CREATE INDEX IF NOT EXISTS idx_request_metrics_created
    ON request_metrics (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_request_metrics_request_id
    ON request_metrics (request_id);

COMMENT ON TABLE request_metrics IS
    '요청 메타데이터 (D-037 허용 열만). 질문 원문·회원 식별자를 넣지 않는다';

COMMIT;

-- 적용 뒤 verify_2026-09-07_request_metrics.sql 을 같은 DB 에 돌린다.
-- 그 스크립트는 **단언형**이라 어긋나면 종료 코드로 실패한다 (#273 규약).
