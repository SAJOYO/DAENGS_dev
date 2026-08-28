-- =====================================================================
-- 2026-08-28_documents_lexical.sql
--
-- documents 에 렉시컬 검색 컬럼을 붙인다 (RAG-003 하이브리드).
-- 원본 스키마는 db/init/01_schema.sql 이고, 이 파일은 **이미 돌고 있는 DB** 용이다.
--
-- 여러 번 실행해도 안전하다.
--
-- ⚠️ **두 단계다.** 컬럼을 NOT NULL 로 바로 못 붙인다 — 기존 행(4,062개)이 비기 때문이다.
--    1단계(이 파일): 컬럼을 기본값과 함께 붙인다. 서비스는 계속 돈다
--    2단계(아래 주석): `rag load` 로 토큰을 채운 **뒤에** 기본값을 떼고 NOT NULL 을 건다
--
--    NOT NULL 이 이 컬럼의 존재 이유다. 안 채운 문서는 렉시컬 반쪽에서 사라지는데
--    **예외가 하나도 안 난다** (dense 로는 계속 찾히니 로그도 조용하다). 2단계를
--    빼먹으면 그 방어가 없는 상태로 남으므로 반드시 같이 돌린다.
-- =====================================================================

BEGIN;

-- 1) 토큰 컬럼. 적재 코드(rag/stages/load.py)가 Kiwi 형태소 결과를 넣는다.
--    기본값은 **2단계에서 뗀다** — 여기서만 기존 행을 채우려고 쓴다.
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS content_tokens TEXT NOT NULL DEFAULT '';

-- 2) tsvector 는 생성 컬럼이라 content_tokens 와 절대 어긋나지 않는다.
--    ADD COLUMN IF NOT EXISTS 는 생성 컬럼에도 그대로 쓸 수 있다.
--
--    'simple' 을 쓰는 이유 — PG 에 한국어 config 가 없다. 형태소 분리는 애플리케이션이
--    끝내고 오므로 여기서는 공백으로만 자르면 된다. 'korean' 을 찾다 없어서 'english'
--    로 두면 한국어에 영어 스테머가 걸려 토큰이 망가진다.
ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS content_tsv tsvector
        GENERATED ALWAYS AS (to_tsvector('simple', content_tokens)) STORED;

COMMENT ON COLUMN documents.content_tokens IS
    'content 를 Kiwi 형태소로 자른 토큰 (공백 구분) / 렉시컬 검색 입력';
COMMENT ON COLUMN documents.content_tsv IS
    'content_tokens 의 tsvector (생성 컬럼) / GIN 인덱스 대상';

COMMIT;


-- =====================================================================
-- 2단계 — 토큰을 채운 뒤에 실행할 것
--
--   uv run python -m daengs_life.rag load --model qwen3-embedding-0.6b
--
-- 로 전 행의 content_tokens 가 찬 것을 확인하고 나서 아래를 돌린다.
-- 확인:
--   SELECT count(*) FROM documents WHERE content_tokens = '';   -- 0 이어야 한다
--
-- =====================================================================
--
-- BEGIN;
--
-- -- 기본값을 떼면 그때부터 안 채운 INSERT 가 실패한다. 이게 목적이다.
-- ALTER TABLE documents ALTER COLUMN content_tokens DROP DEFAULT;
--
-- COMMIT;
--
-- =====================================================================
-- 인덱스는 여기서 만들지 않는다 — db/indexes.sql 이 "적재가 끝난 뒤 수동" 을
-- 못 박아 뒀고, 빈 컬럼에 GIN 을 미리 만들면 채우는 동안 갱신 비용만 든다.
-- ⚠️ CREATE INDEX 는 소유자 권한이라 postgres 로 실행해야 한다 (RAG-030 ⑥).
-- =====================================================================
