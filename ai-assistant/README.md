# 반려견 생활 관리 AI 비서 — MVP (core-rag-mvp)

로컬 Ollama 모델 + PostgreSQL/pgvector 기반 근거 제시형 RAG 챗봇입니다. 질문을 벡터 검색(dense) +
전문검색(keyword)으로 하이브리드 검색한 뒤 LLM 리랭커로 정밀 재정렬하고, 찾은 문서를 근거로 답변을
생성합니다. 근거가 없으면 모른다고 답하도록 설계되어 있습니다(가드레일 이중 방어).

지식베이스는 반려견 상식(음식 위험/급여/건강관리 등, 웹 크롤링 기반)과 **동물보호법 계열 법령**(조 단위로
구조화 청킹, 인용 문자열 포함)으로 구성되어 있습니다. 자세한 요구사항/설계는 `aidlc-docs/`를 참고하세요.

## 사전 준비물

- Docker Desktop (Windows)
- [Ollama](https://ollama.com/download) 설치
- Python 3.12
- [uv](https://docs.astral.sh/uv/) — Python 패키지/가상환경 관리

## 1. 로컬 모델 준비 (Ollama)

```bash
ollama pull bge-m3
ollama pull qwen2.5:7b-instruct
```

다른 모델을 쓰려면 `.env`의 `EMBEDDING_MODEL`/`GENERATION_MODEL`을 변경하세요.
단, 임베딩 모델을 바꾸면 `db/schema.sql`의 `VECTOR(1024)` 차원도 해당 모델의 출력 차원에 맞게 수정해야 합니다.

## 2. DB 실행 (Docker)

```bash
docker compose up -d db
```

`pgvector/pgvector:pg16` 컨테이너가 뜨면서 `db/schema.sql`이 최초 1회 자동 적용됩니다 (documents 테이블 생성).

## 3. Python 의존성 설치

```bash
uv sync
copy .env.example .env
```

`uv sync`가 `pyproject.toml`/`uv.lock` 기준으로 `.venv`를 자동 생성·동기화합니다 (별도 activate 불필요 — 아래 명령들은 `uv run`으로 실행).

## 4. 지식베이스 채우기

`/chat`이 실제로 조회하는 테이블은 `documents_test`입니다(초기 toy 테이블 `documents`는
`/documents`·`/ingest` 엔드포인트 전용 레거시로 분리되어 있음). `scripts/` 아래 여러 인제스트
스크립트로 채웁니다 — 처음이라면 최소한 아래 두 개만 돌려도 `/chat`을 테스트할 수 있습니다:

```bash
set PYTHONPATH=src
uv run python scripts/seed_toy_documents.py          # toy 문장 5건 (최소 동작 확인용)
uv run python scripts/ingest_law_documents.py         # 동물보호법 계열 3개 법령, 조 단위 청킹 359건
```

그 외 `scripts/ingest_*.py`는 각각 다른 소스(dailyvet, PetMD, ASPCA, RDA Open API 등)를 수집합니다.
`RDA_PETFOOD_API_KEY`처럼 특정 스크립트만 쓰는 API 키는 `.env`에 없으면 해당 소스만 자동으로
스킵됩니다.

## 5. 서버 실행

```bash
uv run uvicorn app.main:app --app-dir src --reload
```

Swagger UI: http://localhost:8000/docs

- `GET /health` — 헬스체크
- `POST /chat` — 실제 서빙 중인 질의응답 (`{"query": "포도 먹여도 돼?"}`) — 답변의 `sources[].section`에
  근거 문서의 인용 문자열(예: "동물보호법 제15조제1항")이 함께 반환됩니다.
- `POST /documents`, `GET /documents`, `POST /ingest` — 레거시 `documents` 테이블용 CRUD/실시간 수집

## 6. 테스트

```bash
uv run pytest
```

`tests/test_repository.py`는 실행 중인 DB 컨테이너가 필요합니다 (`docker compose up -d db`).
나머지 테스트(`test_guardrail.py`, `test_rag_service.py`, `test_api.py`)는 외부 의존성을 mock 처리해 DB/Ollama 없이도 실행됩니다.

## 검색 품질 평가

```bash
uv run python scripts/evaluate_search_quality.py     # 일반 질의 27건 — Hit Rate/MRR + LLM-judge 답변 품질
uv run python scripts/evaluate_law_qa_hit_rate.py     # 법령 질의 36건 — 정답 청크 id 정확 일치 기준
```

측정 이력은 `aidlc-docs/construction/build-and-test/`에 누적 저장됩니다
(`search-quality-history.csv`, `law_qa_hit_rate_result.json`).

## 스코프 밖 (다음 로드맵 단계)

- 프로필(견종/나이/체중) 기반 맞춤 답변, 생애주기 판정, 급여량 계산기 (⑭~⑮)
- 정교한 가드레일(분류기 기반) (⑯), Next.js 프론트엔드 (⑲), 배포 (⑳)
- 법령 별표(표)/비고(박스)/법령해석례(Q&A) 청킹 — 현재는 조문(조/항) 단위만 지원

관련 문서: `aidlc-docs/inception/requirements/requirements.md`, `aidlc-docs/inception/user-stories/stories.md`, `aidlc-docs/construction/core-rag-mvp/functional-design/`
