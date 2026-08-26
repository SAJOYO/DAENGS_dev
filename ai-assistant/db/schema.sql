CREATE EXTENSION IF NOT EXISTS vector;

-- embedding 차원(1024)은 기본 임베딩 모델 bge-m3 기준입니다.
-- EMBEDDING_MODEL을 다른 모델로 바꾸면 이 스키마도 함께 맞춰야 합니다.
CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content TEXT NOT NULL,
    embedding VECTOR(1024) NOT NULL,
    source_tag TEXT,
    source_url TEXT,
    -- 하이브리드 검색(키워드 검색)용 전문 검색 컬럼. 'simple' config = 형태소 분석/어간 추출 없이
    -- 공백/구두점 기준으로만 토큰화 (한국어용 별도 사전이 없어 stemming이 부정확하므로,
    -- 정확한 용어(예: "자일리톨", "파보바이러스") 매칭에 집중하는 용도로 사용).
    content_tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS documents_content_tsv_idx ON documents USING GIN (content_tsv);

-- toy 규모(문서 수십 개 이하)에서는 순차 스캔으로 충분합니다.
-- 문서가 수천 개 이상으로 커지면 pgvector ivfflat/hnsw 인덱스도 추가하세요.

-- documents_test: category/subcategory/source 분류 체계로 확장한 스키마.
-- /chat 파이프라인(rag.py/hybrid_search.py/reranker.py)이 이 테이블을 사용한다
-- (repository.py가 쓰는 옛 documents 테이블은 /documents, /ingest 엔드포인트 전용으로 남아있음).
CREATE TABLE IF NOT EXISTS documents_test (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content TEXT NOT NULL,
    embedding VECTOR(1024),
    category VARCHAR(50) NOT NULL,
    subcategory VARCHAR(50) NOT NULL,
    source VARCHAR(100),
    source_type VARCHAR(50),
    document_title TEXT,
    section TEXT,
    source_url TEXT,
    metadata JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ADD COLUMN IF NOT EXISTS로 분리한 이유: documents_test가 이미 (이 컬럼들 없이) 존재하는
-- 환경에서도 이 스크립트를 그대로 재실행하면 안전하게 컬럼이 추가되도록 하기 위함.
ALTER TABLE documents_test
    ADD COLUMN IF NOT EXISTS content_tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED;

ALTER TABLE documents_test
    ADD COLUMN IF NOT EXISTS source_url TEXT;

ALTER TABLE documents_test
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- 컬럼이 이미 metadata JSONB로 들어가 있던 기존 행들(source_url/video_url 키)을 새 컬럼으로 백필.
UPDATE documents_test
SET source_url = COALESCE(metadata->>'source_url', metadata->>'video_url')
WHERE source_url IS NULL;

CREATE INDEX IF NOT EXISTS documents_test_content_tsv_idx ON documents_test USING GIN (content_tsv);

-- 벡터 검색용 HNSW 인덱스. ivfflat 대신 hnsw를 고른 이유: 이 프로젝트는 가끔 대량 벌크 임포트로
-- 데이터 분포가 크게 바뀌는 패턴이라(예: ingest_bulk_dailyvet.py 한 번으로 전체의 94%가 유입됨),
-- 재학습이 필요한 ivfflat보다 삽입할 때마다 그래프에 점진적으로 편입되는 hnsw가 유지보수 부담이
-- 적다. vector_cosine_ops를 쓰는 이유: repository_documents_test.py의 검색 쿼리가 코사인 거리
-- 연산자(<=>)를 쓰기 때문에 인덱스도 같은 거리 함수로 맞춰야 실제로 사용된다.
CREATE INDEX IF NOT EXISTS documents_test_embedding_hnsw_idx
    ON documents_test USING hnsw (embedding vector_cosine_ops);
