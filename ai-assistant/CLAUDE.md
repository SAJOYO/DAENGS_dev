# dog-ai-assistant

반려견 생활 관리 AI 비서 — 로컬 Ollama + PostgreSQL/pgvector 기반 근거 제시형 RAG 챗봇.
자세한 설명은 `README.md` 참고.

## 아키텍처 (src/app/)

요청 흐름: `routers/*.py` (HTTP 껍데기) → `services/rag.py` (retrieve-then-rerank 오케스트레이션)
→ `services/{embedding,hybrid_search,reranker,generation,guardrail}.py` → `repository*.py` (DB).

- `services/rag.py` — 핵심 파이프라인: embed → dense/sparse 검색 → RRF 융합 → LLM 리랭크 → 생성 → 가드레일
- `repository.py` vs `repository_documents_test.py` — **다른 테이블**. `documents`는 초기 toy용 레거시
  (`/documents`, `/ingest` 엔드포인트 전용), 실제 `/chat`이 조회하는 건 `documents_test` 테이블.
  새 기능은 특별한 이유가 없으면 `documents_test` 계열을 사용할 것.
- `services/guardrail.py` (사후 필터) + `services/generation.py`의 시스템 프롬프트 (사전 방어) = 2중 방어.
  둘 다 정규식/프롬프트 기반의 실용적 근사치이며 완벽하지 않음 — 로드맵상 분류기 기반으로 교체 예정.
- `config.py`의 `similarity_threshold`, `rerank_pool_size` 등 튜닝값은 `scripts/evaluate_*.py`로 실측
  후 조정된 값. 임의로 바꾸지 말고, 바꾸면 해당 평가 스크립트로 재검증할 것.

## 컨벤션

- **타입 힌트 필수**: 모든 함수는 매개변수/반환 타입을 명시 (`list[str]`, `str | None` 등 최신 문법 사용).
  단, 정적 타입 체커(mypy 등)는 설정되어 있지 않아 관례로만 지켜지는 중이니 리뷰 시 유의.
- **dataclass vs pydantic**: 외부 입출력(API 요청/응답)은 `schemas.py`의 pydantic `BaseModel`,
  내부 전용 데이터 묶음(예: `rag.py`의 `Source`, `ChatResult`)은 `dataclass` 사용.
- **주석은 "왜"만**: 기존 코드 주석은 비개발자도 이해할 수 있도록 개념 설명 + 과거 실측/사고 이력을
  자세히 남기는 스타일 (예: `config.py`의 threshold 재보정 근거, `language.py`의 실사례). 새 코드도
  임계값·정규식·휴리스틱처럼 "왜 이 값인지" 비자명한 부분은 근거(날짜, 실측 결과)를 남길 것.

## 개발 명령어

```bash
docker compose up -d db                                    # DB 실행 (최초 1회 db/schema.sql 자동 적용)
uv sync                                                     # 의존성 설치
uv run uvicorn app.main:app --app-dir src --reload          # 서버 실행 (Swagger: /docs)
uv run pytest                                                # 테스트 (pytest.ini가 pythonpath=src 설정)
uv run python scripts/evaluate_search_quality.py             # 검색 품질 평가 (Hit Rate/MRR)
```

`tests/test_repository.py`는 실행 중인 DB 컨테이너가 필요; 나머지는 DB/Ollama를 mock 처리.

## 문서

요구사항/설계/변경 이력은 `aidlc-docs/`에 있음 (AIDLC 워크플로우 산출물). 특히:
- `aidlc-docs/inception/requirements/requirements.md` — 요구사항
- `aidlc-docs/construction/build-and-test/` — 검색 품질 측정 이력 (`search-quality-history.csv` 등)

## 1. 기본 원칙

- 기존 코드를 먼저 확인한 후 수정한다.
- 사용자가 요청한 범위 내에서만 변경한다.
- 기존 기능을 임의로 삭제하거나 변경하지 않는다.
- 새로운 파일이나 폴더를 만들기 전에 기존 프로젝트 구조를 확인한다.
- 작업 완료 전에 변경된 파일과 변경 내용을 간단히 요약한다.
- 불확실한 부분은 임의로 결정하지 말고 사용자에게 질문한다.

## 2. Python

- Python 3.12를 사용한다.
- 모든 함수와 메서드에 타입 힌트를 작성한다.
- 함수의 매개변수와 반환 타입을 명시한다.
- Any 타입은 꼭 필요한 경우가 아니면 사용하지 않는다.
- 전역 변수 사용을 최소화한다.
- 하나의 함수가 너무 많은 책임을 가지지 않도록 한다.
- 매직 넘버와 매직 문자열 사용을 최소화한다.

예:

def get_dog_profile(dog_id: UUID) -> DogProfile:
    ...


## 3. FastAPI

- API Request/Response 모델은 Pydantic을 사용한다.
- API Response의 타입을 명확하게 정의한다.
- 비즈니스 로직을 API Router에 직접 작성하지 않는다.
- Router / Service / Repository 계층을 분리한다.
- HTTP 상태 코드를 적절하게 사용한다.
- 예외 상황은 명확한 HTTPException 또는 프로젝트의 공통 예외 처리 방식을 사용한다.


## 4. Pydantic

- API 입력과 출력 데이터는 Pydantic 모델을 우선적으로 사용한다.
- 데이터 검증은 가능한 한 Pydantic 모델에서 처리한다.
- 동일한 데이터 구조를 여러 곳에서 중복 정의하지 않는다.


## 5. 데이터베이스

- PostgreSQL을 사용한다.
- ORM은 SQLAlchemy를 사용한다.
- 데이터베이스 접근 로직은 Repository 계층에서 관리한다.
- 기존 DB 스키마를 임의로 변경하지 않는다.
- DB 구조 변경이 필요한 경우 먼저 변경 이유를 설명한다.
- SQL Injection이 발생하지 않도록 ORM 또는 Parameterized Query를 사용한다.


## 6. RAG / AI

- RAG 관련 코드를 일반 비즈니스 로직과 분리한다.
- 문서 로딩 → Chunking → Embedding → Retrieval → Reranking → Generation 단계를 명확하게 분리한다.
- Embedding 모델을 코드에 하드코딩하지 않는다.
- LLM 모델명을 환경설정으로 관리한다.
- 검색 결과와 LLM 응답을 혼합하여 임의의 사실을 생성하지 않는다.
- RAG 응답에는 가능한 경우 검색된 근거 정보를 함께 제공한다.
- Prompt는 코드에 직접 하드코딩하지 않고 별도의 파일 또는 관리 가능한 구조로 분리한다.


## 7. 환경변수 / 보안

- API Key, 비밀번호, 토큰 등의 민감정보를 코드에 직접 작성하지 않는다.
- 민감한 설정은 .env 또는 환경변수를 사용한다.
- .env 파일을 Git에 커밋하지 않는다.
- 기존 API Key나 Secret을 임의로 변경하지 않는다.
- 실제 Secret 값을 코드나 로그에 출력하지 않는다.


## 8. 의존성 관리

- 새로운 Python 라이브러리를 추가하기 전에 기존 의존성을 확인한다.
- 동일한 기능을 수행하는 라이브러리를 중복으로 설치하지 않는다.
- 필요하지 않은 라이브러리는 설치하지 않는다.
- 패키지 설치 후 requirements.txt 또는 pyproject.toml을 업데이트한다.


## 9. 코드 품질

- Ruff 기준에 맞춰 코드를 작성한다.
- 사용하지 않는 import와 변수를 제거한다.
- 중복 코드를 최소화한다.
- 지나치게 긴 함수를 만들지 않는다.
- 코드의 가독성을 우선한다.
- 주석은 코드가 "무엇을 하는지"보다 "왜 이렇게 하는지" 설명할 때 사용한다.


## 10. 테스트

- 새로운 기능을 추가하면 관련 pytest 테스트를 작성한다.
- 기존 기능을 수정하면 기존 테스트가 깨지지 않는지 확인한다.
- 테스트가 실패하면 원인을 확인하고 수정한다.
- 테스트를 통과하지 않은 상태에서 작업 완료라고 판단하지 않는다.


## 11. Git

- 사용자가 요청하지 않은 git commit을 하지 않는다.
- 사용자가 요청하지 않은 git push를 하지 않는다.
- 기존 commit을 임의로 수정하거나 삭제하지 않는다.
- 작업 전후 git diff를 확인한다.


## 12. 작업 절차

새로운 기능을 구현할 때 다음 순서를 따른다.

1. 관련 파일과 프로젝트 구조를 먼저 확인한다.
2. 기존 구현 방식을 파악한다.
3. 필요한 변경사항을 간단하게 정리한다.
4. 코드를 수정한다.
5. 테스트를 작성하거나 수정한다.
6. pytest를 실행한다.
7. ruff check를 실행한다.
8. 문제가 있으면 수정하고 다시 테스트한다.
9. 최종적으로 변경된 파일과 내용을 요약한다.


## 13. 금지사항

- 사용자의 요청 없이 프로젝트 전체 구조를 변경하지 않는다.
- 사용자의 요청 없이 프레임워크를 변경하지 않는다.
- 사용자의 요청 없이 DB를 초기화하지 않는다.
- 사용자의 요청 없이 데이터를 삭제하지 않는다.
- 사용자의 요청 없이 새로운 외부 서비스를 추가하지 않는다.
- 사용자의 요청 없이 패키지를 대량으로 설치하지 않는다.
- 테스트를 생략하고 작업을 완료했다고 판단하지 않는다.
