-- =====================================================================
-- 04_crawl_runs.sql
-- 크롤 실행 이력 (RAG-001 원칙 3 · RAG-047)
-- 실행 순서: 01_schema.sql -> 02_trigger.sql -> 03_auth.sql -> 04_crawl_runs.sql
-- =====================================================================

-- ---------------------------------------------------------------------
-- crawl_runs : 크롤 태스크가 소스 하나를 돌 때마다 한 행
-- ---------------------------------------------------------------------
-- **`data/manifests/crawl_log.jsonl` 을 대체하지 않는다.** 로그는 크롤러가 단독 실행될
-- 때도 남아야 하고(RAG-001 원칙 1), due 판정이 그것을 읽는다(RAG-044 ③). 이 테이블은
-- 그 위의 **요약**이고, 쓰는 쪽은 `tasks/` 다 — `crawler` 패키지는 DB 를 모른다.
-- due 판정을 이 테이블로 옮기면 `python -m crawler` 가 DB 없이는 안 도는 날이 온다.
--
-- 행 단위가 '태스크 실행'이 아니라 '소스'인 이유: `crawl_due` 한 번이 소스 여럿을 돌고,
-- 관리자 화면이 보는 것은 "소스별 마지막 실행"이다. 태스크 단위로 묶으면 그 질의가
-- JSON 을 파고들어야 한다.
CREATE TABLE IF NOT EXISTS crawl_runs (
    id BIGSERIAL PRIMARY KEY,

    -- crawler 가 붙이는 실행 id (예: 20260829-145804). 소스 하나의 수집 한 번에 하나.
    -- `crawl_log.jsonl` 의 같은 이름 필드와 맞물린다 — 이 테이블에서 로그로 내려가는 다리다.
    -- **NULL 을 허용한다.** 이 값은 수집이 끝나야 나오는데 행은 시작할 때 넣기 때문이다
    -- (status='running'). NULL = 아직 안 끝났거나, 끝나기 전에 죽었다는 뜻이다.
    run_id TEXT,

    source_id TEXT NOT NULL,

    -- 'due'      = Beat 가 주기 판정으로 고른 것
    -- 'manual'   = 관리자가 이름을 대고 부른 것 (RAG-001 요구사항 ②③)
    -- 'revision' = Beat 가 개정 판정으로 깨운 것 — cadence 가 manual 인 법령은 이 길로만 받는다 (RAG-054)
    -- 같은 함수를 지나가지만 화면에서 갈라 보여야 한다.
    trigger TEXT NOT NULL
        CHECK (trigger IN ('due','manual','revision')),

    -- 'unavailable' 을 'failed' 와 가른다. 키 미설정·시드 URL 사망은 **실패가 아니라
    -- "아직 못 하는 것"** 이라 사람이 고쳐야 하고, 화면에서 다르게 보여야 한다.
    -- 'running' 은 시작 시점에 넣고 끝나면 갱신한다 — 워커가 죽으면 이 상태로 남는다.
    status TEXT NOT NULL
        CHECK (status IN ('running','ok','failed','unavailable')),

    docs_fetched INT NOT NULL DEFAULT 0,
    docs_changed INT NOT NULL DEFAULT 0,
    docs_failed  INT NOT NULL DEFAULT 0,
    docs_skipped INT NOT NULL DEFAULT 0,

    -- 바뀐 문서의 slug. C3(법령·약관 개정 감지)의 입력이다 — 그쪽이 "무엇이 바뀌었나"를
    -- 여기서 읽는다. 적재로 이어 붙이지 않는 것은 RAG-002 · RAG-025 의 판단이다.
    changed_slugs TEXT[] NOT NULL DEFAULT '{}',

    -- status='failed' 면 예외 문자열, 'unavailable' 이면 사람이 고칠 안내.
    error TEXT,

    started_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ
);

-- 관리자 화면의 기본 질의: 소스별 최신 실행. 그래서 (source_id, started_at DESC) 다.
CREATE INDEX IF NOT EXISTS idx_crawl_runs_source_started
    ON crawl_runs (source_id, started_at DESC);

-- 실행 하나를 통째로 보는 질의 (한 번의 crawl_due 가 무엇을 돌았나).
CREATE INDEX IF NOT EXISTS idx_crawl_runs_run_id
    ON crawl_runs (run_id);

COMMENT ON TABLE  crawl_runs               IS '크롤 실행 이력 (소스 하나당 한 행). crawl_log.jsonl 위의 요약';
COMMENT ON COLUMN crawl_runs.run_id        IS 'crawler 가 붙인 실행 id / crawl_log.jsonl 과 맞물린다';
COMMENT ON COLUMN crawl_runs.trigger       IS 'due=Beat 주기 판정 / manual=관리자 수동 트리거';
COMMENT ON COLUMN crawl_runs.status        IS 'running/ok/failed/unavailable. unavailable 은 사람이 고쳐야 하는 것';
COMMENT ON COLUMN crawl_runs.changed_slugs IS '바뀐 문서 slug / C3(개정 감지)의 입력';
COMMENT ON COLUMN crawl_runs.error         IS 'failed 면 예외 문자열, unavailable 이면 사람이 고칠 안내';
