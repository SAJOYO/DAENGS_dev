-- crawl_runs.trigger 에 'revision' 을 허용한다 (RAG-054, #66).
--
-- Beat 가 개정 판정으로 깨운 수집을 'due' · 'manual' 과 갈라 화면에 보이기 위해서다.
-- 안 걸면 워커의 기록이 CHECK 에 막혀 조용히 빠진다 — 기록 실패는 크롤을 죽이지 않는
-- 설계라(RAG-047 ②) 수집은 되고 관리자 화면에만 안 보인다.
--
-- 여러 번 돌려도 안전하다 (DROP IF EXISTS → ADD). db/init/04_crawl_runs.sql 과 같이 고쳤다.
--
--   docker compose exec -T pgvector psql -U postgres -d vectordb < db/migrations/2026-08-30_crawl_runs_trigger_revision.sql

ALTER TABLE crawl_runs DROP CONSTRAINT IF EXISTS crawl_runs_trigger_check;
ALTER TABLE crawl_runs
    ADD CONSTRAINT crawl_runs_trigger_check CHECK (trigger IN ('due', 'manual', 'revision'));
