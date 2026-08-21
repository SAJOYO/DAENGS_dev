-- =====================================================================
-- 01_schema.sql
-- 생활비서 RAG - documents 테이블 스키마
-- 실행 순서: 01_schema.sql -> 02_trigger.sql -> (데이터 적재) -> indexes.sql
-- =====================================================================

-- pgvector 확장 활성화 (VECTOR 타입 사용을 위해 필수)
CREATE EXTENSION IF NOT EXISTS vector;


-- ---------------------------------------------------------------------
-- documents : RAG 검색 대상 문서 청크
-- ---------------------------------------------------------------------
CREATE TABLE documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content TEXT NOT NULL,

    -- content(청크 텍스트)의 SHA-256 hex 64자. 재적재 시 중복 방지용 자연키.
    --   INSERT ... ON CONFLICT (content_hash) DO NOTHING
    -- NULL을 허용하면 UNIQUE가 NULL끼리는 중복으로 보지 않아 방어가 뚫리므로 NOT NULL 필수.
    -- D-001 원칙2의 '콘텐츠 sha256'과는 층위가 다르다.
    --   원본 문서 해시 -> .meta.json (재파싱/재임베딩 스킵 판단)
    --   청크 해시      -> 이 컬럼      (중복 적재 방지)
    content_hash CHAR(64) NOT NULL UNIQUE,

    embedding VECTOR(1024),

    -- care/emergency는 진단 성격이 강해 별도 에이전트로 분리(D-008). 필요해지면 제약만 교체하면 된다.
    -- 접종 스케줄처럼 '법정 의무가 아닌' 문서는 category가 아니라 metadata.trust_level로 구분한다.
    category VARCHAR(50) NOT NULL
        CHECK (category IN ('policy','travel','food')),

    -- CHECK 없음. 값은 data/README.md의 값 사전에서 관리하며 수집하면서 늘려간다 (kebab-case).
    -- 재분류는 UPDATE 한 줄이고 content가 안 바뀌므로 임베딩 재계산이 필요 없다.
    subcategory VARCHAR(50) NOT NULL,

    source VARCHAR(100),

    -- 파일 포맷이 아니라 '매체 유형'이다. (pdf/hwp 같은 포맷은 metadata.format)
    -- 기존 'pdf'는 포맷 축이라 'web'과 기준이 섞였다(웹에서 받은 PDF 문제) -> 'document'로 통일.
    --   document : 파일로 배포된 문서 (공고문 PDF/HWP/HWPX, 약관)
    --   web      : HTML 본문 (기관 웹페이지, 해설 페이지)
    --   api      : API 응답 (법제처 DRF XML, 공공데이터포털 JSON)
    --   manual   : 직접 작성/정리 (FAQ, 보정 데이터)
    -- video/audio는 해당 소스를 실제로 수집할 때 추가한다 (현재 시드 30개에 없음).
    source_type VARCHAR(50)
        CHECK (source_type IN ('document','web','api','manual')),
    source_url TEXT,
    document_title TEXT,
    section TEXT,

    -- ----- metadata 표준 키 (DB는 강제하지 않음 / 적재 코드의 Pydantic 모델에서 검증) -----
    --   raw_file        : data/raw 기준 상대경로 (원문 역추적)              [필수]
    --   format          : pdf|hwp|hwpx|html|xml|json                        [필수]
    --   trust_level     : law|official|guideline                            [필수]
    --                     law(법령) > official(공공기관 안내) > guideline(협회/수의학 가이드)
    --                     policy 질의를 법정 근거 문서로 한정할 때 쓴다
    --   embedding_model : 임베딩 모델 식별자 (D-002 - 한 컬럼에 모델 혼입 금지) [임베딩 후 필수]
    --   published_at    : 공고일/시행일 (created_at은 적재 시각이라 다름)
    --   license         : 공공누리 유형 등
    --   page            : 원본 페이지 번호 [source_type=document]
    --   site            : 수집 도메인
    -- ---------------------------------------------------------------------------------
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ---------------------------------------------------------------------
-- 컬럼 설명 (\d+ documents 또는 DBeaver/pgAdmin에서 확인 가능)
-- ---------------------------------------------------------------------
COMMENT ON TABLE  documents                 IS '생활비서 RAG 문서 청크';

COMMENT ON COLUMN documents.id              IS 'UUID로 생성되는 문서/Chunk 고유 ID';
COMMENT ON COLUMN documents.content         IS '실제 RAG 검색 대상이 되는 Chunk';
COMMENT ON COLUMN documents.content_hash    IS 'content의 SHA-256 hex 64자 / 재적재 중복 방지용 자연키';
COMMENT ON COLUMN documents.embedding       IS '1024차원 벡터';
COMMENT ON COLUMN documents.category        IS '대분류';
COMMENT ON COLUMN documents.subcategory     IS '세부 분류 (kebab-case. 값 사전은 data/README.md)';
COMMENT ON COLUMN documents.source          IS '콘텐츠 생성 주체 (기관명 등)';
COMMENT ON COLUMN documents.source_type     IS '매체 유형 (파일 포맷 아님. 포맷은 metadata.format)';
COMMENT ON COLUMN documents.source_url      IS '원문 URL/출처 링크';
COMMENT ON COLUMN documents.document_title  IS '원본 문서 제목';
COMMENT ON COLUMN documents.section         IS '원본 문서의 장/절 (D-004: 제16조제2항 형식)';
COMMENT ON COLUMN documents.metadata        IS
'메타 데이터/부가 정보. 표준 키(DB 미강제, 적재 코드에서 검증):
 raw_file, format, trust_level = 모든 행 필수 / embedding_model = 임베딩 후 필수
 published_at, license, page, site = 선택';
COMMENT ON COLUMN documents.created_at      IS '생성/저장 시각';
COMMENT ON COLUMN documents.updated_at      IS '수정 시각';
