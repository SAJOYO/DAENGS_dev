# Training RAG 데모 통합

## 범위와 호출 흐름

훈련 RAG는 DAENGS 백엔드 안에 포함하지 않고 별도 FastAPI 서비스로 실행한다. 브라우저는
RAG에 직접 접근하지 않는다.

```text
DAENGS 관리자 콘솔 `검색 점검` 화면 (Next.js)
  -> /api/training/chat
DAENGS 메인 FastAPI
  -> Training RAG FastAPI /chat
  -> dog-rag-pgvector + Ollama gemma3:4b
```

RAG는 검수된 서비스 문서 14개와 기존 PGVector 색인을 그대로 사용한다. 재색인, sibling 확장,
metadata filter, semantic reranker는 이 데모 범위에 포함하지 않는다. 현재 RAG `/chat` 계약에
반려견 식별자가 없으므로 `dog_id`는 전달하지 않는다.

## 공개 API 계약

DAENGS는 내부 RAG의 `chunk_id`, 검색 점수, gate 진단을 브라우저에 보내지 않는다.

```http
POST /training/chat
Content-Type: application/json

{"question":"배변 훈련은 어떻게 시작하나요?"}
```

```json
{
  "decision": "ANSWER",
  "answer": "...",
  "citations": [{"rank": 1, "label": "예절교육"}]
}
```

`decision`은 다음 네 경우만 공개한다.

- `ANSWER`: 근거 기반 훈련 안내
- `UNCERTAIN`: 현재 문서만으로 정확한 안내가 어려움
- `SAFETY_REFUSAL`: 체벌·임의 투약처럼 안내하지 않기로 한 요청
- `MEDICAL_REFUSAL`: 수의학적 판단이 필요한 질문

`SAFETY_REFUSAL`과 `UNCERTAIN`을 가르는 것은 상류 RAG의 `reason`이다. 둘 다 상류에서는
`REFUSE`로 오지만 사용자에게 뜻이 다르다 — `UNCERTAIN`은 자료가 모자라다는 뜻이라 자료가
늘면 답할 수 있고, `SAFETY_REFUSAL`은 자료가 늘어도 답하지 않는다. 한 상태로 접으면 안전
거절이 "현재 자료 범위 안내"로 표시된다.

| 상류 `decision` | 상류 `reason` | 공개 `decision` |
| --- | --- | --- |
| `REFUSE` | `safety_boundary_training_harm` | `SAFETY_REFUSAL` |
| `REFUSE` | `safety_boundary_medical` | `MEDICAL_REFUSAL` |
| `REFUSE` | `no_results` · 그 밖 · 없음 | `UNCERTAIN` |

응답과 장애 응답에는 `X-Request-ID`가 포함된다. 메인 백엔드는 질문 원문을 로그에 남기지 않고,
요청 식별자·상태·인용 수만 기록한다. 내부 RAG의 `REFUSE`는 외부 계약에서 `UNCERTAIN`으로
정규화한다.

기본값은 **관리자 토큰**과 `Perm.SEARCH_INSPECT` 권한을 요구한다 (#30). 부르는 곳이 콘솔의
`검색 점검` 화면 하나뿐인 임시 게이트웨이라 앱 회원 경로는 열어 두지 않는다 — 앱이 쓰는
`/walk` 과 다른 점이다. 권한이 없는 관리자는 403, 앱 회원 토큰은 401 이다.

`DAENGS_TRAINING_RAG_ALLOW_ANONYMOUS_DEMO=true` 는 RAG 를 단독으로 만질 때만 쓴다. 이 값은
공유·운영 환경에서 `true`로 두면 안 되고, `docker-compose.yml` 이 backend 로 넘기지 않으므로
서버에서는 최상단 `.env` 로 켤 수도 없다.

## 환경 변수

DAENGS 백엔드:

```dotenv
DAENGS_TRAINING_RAG_BASE_URL=http://127.0.0.1:8010
DAENGS_TRAINING_RAG_CONNECT_TIMEOUT_SECONDS=3
DAENGS_TRAINING_RAG_READ_TIMEOUT_SECONDS=45
DAENGS_TRAINING_RAG_ALLOW_ANONYMOUS_DEMO=false
```

DAENGS backend를 Docker Compose로 실행하고 RAG를 Windows 호스트에서 실행할 때에는 root `.env`에
다음을 사용한다.

```dotenv
DAENGS_TRAINING_RAG_BASE_URL=http://host.docker.internal:8010
```

이 값은 `backend/.env`가 아니라 **Compose를 실행하는 배포 checkout의 최상단 `.env`**에
반드시 있어야 한다. Compose의 `environment:`가 `env_file:`보다 우선하므로 최상단 값이
없으면 빈 문자열이 backend 컨테이너에 주입된다. 배포 workflow는 이제 `docker compose config`
단계에서 이 누락을 먼저 실패시킨다.

Linux Docker에서는 backend 서비스가 `host.docker.internal:host-gateway`를 명시적으로
매핑한다. RAG 프로세스가 호스트에서 실행된다면 컨테이너가 닿을 수 있도록 `0.0.0.0:8010`에
바인딩하고, `http://127.0.0.1:8010/healthz`로 정상 응답하는지 확인한다.

DAENGS의 `pgvector:5432/vectordb`와 RAG의 `dog-rag-pgvector:5433/dog_rag`는 서로 다른 데이터베이스다.
DAENGS Compose는 RAG DB를 만들거나 변경하지 않는다.

## 로컬 데모 실행

1. 별도 RAG 서비스를 시작한다. RAG 저장소에서만 실행한다.

   ```powershell
   cd C:\Users\804\Documents\workspace\dog-training-rag-retrieval
   docker compose -f docker-compose.pgvector.yml up -d
   ollama ps
   # qwen3.5:9b 등이 실제로 로드돼 있으면: ollama stop qwen3.5:9b
   uv run uvicorn scripts.rag_api:app --host 127.0.0.1 --port 8010
   ```

   다른 PowerShell에서 상태와 warm-up을 확인한다.

   ```powershell
   Invoke-RestMethod http://127.0.0.1:8010/healthz
   Invoke-RestMethod http://127.0.0.1:8010/chat -Method Post -ContentType 'application/json' `
     -Body '{"question":"배변 훈련은 어떻게 시작하나요?","top_k":4}'
   ```

   RTX 3050 6GB 데모에서는 `gemma3:4b`만 로드하고 동시 생성은 1건을 권장한다. warm-up 뒤에
   `ollama ps`로 모델을 다시 확인한다.

2. DAENGS 백엔드를 실행한다.

   ```powershell
   cd C:\Users\804\Documents\workspace\DAENGS_dev\backend
   Copy-Item .env.example .env
   # .env의 DB/키 값을 채운다. 관리자로 로그인해 부를 것이므로 익명 플래그는 false로 둔다.
   # RAG 를 단독으로 만질 때만: DAENGS_TRAINING_RAG_ALLOW_ANONYMOUS_DEMO=true
   uv sync
   uv run dev
   ```

3. 프론트엔드를 실행하고 `http://localhost:3000/console/search` 를 연다. 관리자로 로그인해야 열린다.

   ```powershell
   cd C:\Users\804\Documents\workspace\DAENGS_dev\frontend
   npm ci
   npm run dev
   ```

Next.js는 `/api/training/chat`을 메인 백엔드로 rewrite한다. 따라서 화면은 RAG URL이나 검색 점수에
접근하지 않는다.

## 데모 점검 질문

- `배변 훈련은 어떻게 시작하나요?` — `ANSWER` 예상
- `산책 중 줄을 당길 때 어떻게 훈련하나요?` — `ANSWER` 또는 문서 근거에 따른 `UNCERTAIN`
- `강아지가 구토하는데 어떤 약을 먹여야 하나요?` — `MEDICAL_REFUSAL` 예상
- 근거가 없는 임의의 전문 훈련 질문 — `UNCERTAIN` 예상

RAG 자체 상태는 `GET http://127.0.0.1:8010/healthz`로 확인한다. 메인 백엔드의 기존 health endpoint도
별도로 확인한다. RAG 연결 실패는 503, 생성 read timeout은 504로 사용자에게 재시도 안내를 보낸다.

## 통합 스모크 실행 기록

2026-08-27 로컬에서 RAG `/healthz`는 `gemma3:4b`, 서비스 문서 14개로 정상 응답했다. DAENGS의
임시 backend `:8090`과 Next.js `:3001`을 사용해 아래 경로를 실제로 호출했으며, 두 임시 프로세스는
검증 후 종료했다.

| 질문 종류 | 결과 | 지연 |
| --- | --- | --- |
| 배변 훈련 answerable | `ANSWER`, 인용 4개 | 9.85초 |
| 약 복용량 의료 질문 | `MEDICAL_REFUSAL`, 인용 0개 | 0.08초 |
| 품종 일반화 근거 부족 질문 | `UNCERTAIN`, 인용 4개 | 4.09초 |
| Next `/api/training/chat` rewrite를 통한 의료 질문 | `MEDICAL_REFUSAL` | 0.11초 |

이 결과는 warm 상태의 단일 요청 측정이며, 성능 벤치마크가 아니다. UI는 요청 중 입력과 버튼을 비활성화하고,
`ANSWER`·`UNCERTAIN`·`MEDICAL_REFUSAL`·timeout/장애를 서로 다른 상태로 표시한다.

## 검증과 롤백

```powershell
cd backend
uv run pytest

cd ..\frontend
npm run lint
npm run build
```

롤백하려면 `feature/training-rag-integration`의 아래 변경만 되돌린다. RAG 저장소·PGVector 볼륨·평가셋은
변경하지 않는다.

- `backend/src/daengs_backend/routers/training.py`
- `backend/src/daengs_backend/services/training_rag.py`
- `backend/src/daengs_backend/schemas/training.py`
- `backend/src/daengs_backend/main.py`
- `frontend/app/components/training-chat.tsx`, `frontend/app/console/search/page.tsx`
- 관련 환경 변수와 `docker-compose.yml`

그 뒤 backend와 frontend를 재시작한다.
