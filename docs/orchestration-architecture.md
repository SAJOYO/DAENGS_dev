# 오케스트레이션 아키텍처

서비스가 늘면서 "무엇이 어디서 어떻게 뜨는지"가 docker-compose.yml 주석 · nginx 설정 ·
README · CLAUDE.md 에 흩어졌습니다. 이 문서는 그 전체 지도를 한 장으로 모은 것입니다.
개별 함정의 상세(왜 그 줄이 그렇게 생겼는지)는 각 파일의 주석이 원본이고, 여기서는
구조와 "왜 이렇게 나눴는지"만 다룹니다.

문서는 셋으로 나뉩니다. **이 파일** = 물리 토폴로지(§1~§5, 전부 CURRENT 사실) +
논리 오케스트레이션 구조(§6~, CURRENT 와 TARGET 을 구분해 표기).
**[orchestration-contracts.md](orchestration-contracts.md)** = 오케스트레이터 공통 계약 (확정).
**[orchestration-routing.md](orchestration-routing.md)** = 라우팅 정책 · 인가 매트릭스 · 사람 결정 이력.

> 표기: **CURRENT** = 지금 사실 · **TARGET** = 승인된 목표 상태(아직 구현 안 됨) ·
> **CONFIRMED** = 확정된 설계 제약 · **OPEN** = 사람 결정 대기 · **PENDING** = 검증 대기 ·
> **FOLLOW-UP** = 별도 카드로 후속.

## 실행 주체는 셋입니다

서버 PC(Windows) 한 대 위에서 세 가지 방식으로 프로세스가 돕니다. 하나로 합치지 않은
것은 각각 이유가 있습니다. (Training RAG 가 호스트 단독 프로세스(`:8010`)였던 넷째 주체는
#83·#94 로 사라졌습니다 — backend 프로세스 안의 `daengs_training` 모듈이 됐습니다.)

| 주체 | 무엇을 띄우나 | 왜 여기인가 |
| --- | --- | --- |
| **Docker Compose** | nginx · backend · pgvector · redis · place-search · place-db · journey-service · crawler-worker · crawler-beat (+ profile 뒤의 것들) | 리눅스 컨테이너로 통일된 런타임. `restart: unless-stopped` 라 Docker Desktop 이 뜨면 같이 살아납니다 |
| **PM2 (호스트)** | Next.js 프론트 (`daengs-web`, cluster ×2) | standalone 빌드를 releases 폴더로 무중단 교체하는 배포 방식(아래 §4)이 호스트 프로세스를 전제로 합니다 |
| **self-hosted GitHub Actions 러너** | 배포 워크플로우 (`deploy.yml`) | 배포 대상이 이 PC 자신이라 러너도 이 PC 에 있습니다. 러너가 꺼져 있으면 배포는 대기 상태로 멈춥니다 |

기본 기동에서 FastAPI 프로세스는 **셋**입니다: `backend`, `place-search`,
`journey-service`. Training·Life·Walk·Skin 은 `backend` 한 프로세스 안의 모듈/라우터이고,
Place·Journey 는 소스와 lock 만 backend 프로젝트에 합쳤을 뿐 각자 별도 프로세스를
유지합니다 (D-039). `gait-analysis` 는 `gait` profile 을 켰을 때만 추가되는 넷째
FastAPI 프로세스입니다. self-hosted 러너는 배포 주체이지 요청 처리 프로세스가 아닙니다.

크롤링(Celery worker · beat)은 #65 로 profile 이 떨어져 기본 세트로 뜹니다 — 매일
KST 04:00 due 소스만 수집하고 거기서 멈춥니다 (RAG-044 ⑤ · RAG-050, 루트 README 참고).

## 요청이 지나는 길

바깥에 열린 포트는 nginx 의 **80 과 8000 둘뿐**입니다. 백엔드 계열 컨테이너는 포트를
열지 않고(D-005), 새 서비스가 생겨도 포트를 늘리지 않고 8000 에 경로를 얹습니다 —
공유기 포트포워딩·방화벽·DNS 를 건드릴 일이 없고 HTTPS 도 나중에 한 번에 붙습니다.

```
                     서버 PC (Windows)
                     ┌────────────────────────────────────────────────┐
 daengs.~    :80 ──▶ │ nginx(도커)                                     │
                     │   /            ─▶ host.docker.internal:3000 ───┼─▶ PM2: daengs-web (Next standalone ×2)
                     │   /api/*       ─▶ backend:8000  (접두사 제거)   │
                     │                                                │
 daengback.~ :8000 ─▶│   /v2/places/* ─▶ place-search:8000  (rate limit)
                     │   /journey     ─▶ journey-service:8000         │
                     │   /screen/*    ─▶ backend:8000  (daengs_screening)
                     │   /gait/*      ─▶ gait-analysis:8000   (profile)
                     │   그 외        ─▶ backend:8000                 │
                     └────────────────────────────────────────────────┘
                          backend (Training · Life · Walk · Skin)
                                  ─▶ pgvector:5432 (vectordb, Training 전용 테이블 포함) · redis:6379
                                  ─▶ Gemini API (Training·Life 생성)
                          place-search ─▶ place-db:5432 (자기 전용 PostGIS)
```

경로별 규칙 요약 (원본과 상세 이유는 `nginx/default.conf` 주석):

| 진입 | 경로 | 대상 | 비고 |
| --- | --- | --- | --- |
| :80 | `/` | PM2 의 Next | upstream 블록 사용 — 게이트웨이 IP 라 재해석 불필요 |
| :80 | `/api/*` | backend | 접두사를 rewrite 로 뗍니다. 로그인(httpOnly 쿠키)의 전제인 같은-오리진 경로 (D-015) |
| :8000 | `/v2/places/*` | place-search | 공개 검색이라 IP 당 5r/s 제한 (D-028). 접두사 제거 없음 |
| :8000 | `/journey` | journey-service | URI·본문 무변환. Place 의 rate limit 을 여기로 넓히지 않습니다 |
| :8000 | `/screen/*` | backend (`daengs_screening`) | 접두사 제거 없음. main backend 의 무인증 multipart 라우터이며 가중치는 첫 요청에 지연 로딩 (D-040) |
| :8000 | `/gait/*` | gait-analysis | profile 뒤. 여기만 body 200m · timeout 600s (영상) |
| :8000 | 그 외 | backend | 접두사 제거 없음 |

컨테이너 대상 경로는 전부 **upstream 블록이 아니라 `set` 변수 + resolver** 입니다.
compose 가 컨테이너를 재생성하면 IP 가 바뀌는데, upstream 블록은 nginx 기동 때 한 번만
이름을 풀어서 재생성마다 502 가 났기 때문입니다 (#31). 새 경로를 추가할 때도 같은 모양
(`set` 이 `rewrite` 보다 먼저)을 지켜야 합니다.

## Compose 서비스 지도

`docker compose up -d` 로 뜨는 기본 세트와, profile 을 명시해야 뜨는 것들이 나뉩니다.

**기본 기동** — nginx · backend · pgvector · redis · place-search · place-db ·
journey-service · crawler-worker · crawler-beat

- Skin 은 더 이상 별도 compose 서비스/profile 이 아닙니다 (#100, D-040). 소스는
  `backend/src/daengs_screening/`, 라우터는 main backend 의 `/screen/*` 에 등록되고,
  가중치 디렉터리도 backend 에 read-only 로 마운트됩니다. backend 는 기동 때
  `ml` 과 `screening` dependency group 을 함께 동기화하며, #101 이 빠져 있던 screening
  lock 항목을 복구했습니다. 모델은 backend 기동이 아니라 첫 `/screen/v1/screen`
  요청 때 로드됩니다.
- backend 는 pgvector·redis 의 **healthy 를 기다립니다.** PGVector 컨테이너는
  **하나**입니다 — Training 전용이던 `training-rag-pgvector` 컨테이너는 #105 로,
  Training 전용 `dog_rag` **데이터베이스**는 #112 로 없어졌습니다. Training 은 이제
  본체와 **같은 `vectordb` DB** 를 쓰고, `public.training_rag_documents`/
  `training_rag_chunks` 테이블로만 나뉩니다. 스키마 원본은 여전히
  `backend/infra/training_pgvector/schema.sql`(768차원)이고, 본체 DB 규칙과 같게
  `db/init/05_training_rag.sql` 에도 같은 정의가 있습니다 (#92·#94·#105·#112 —
  상세는 아래 "Training 토폴로지" 절). redis 는 없어도 앱이 뜨지만,
  캐시 폴백 판단이 프로세스 생애에 한 번뿐이라 순서를 보장해야 일 예산 카운터가
  동작합니다 (D-019).
- place-search 는 place-db(PostGIS) healthy 후 **Alembic 을 돌리고 나서** 서버를 띄웁니다.
  place 스키마의 원본은 Alembic 이고, 이는 "스키마 원본은 `db/init/*.sql`" 이라는 본체 DB
  규칙의 의도된 예외입니다 (D-026).
- pgvector 와 redis 는 **일부러 LAN 에 열려 있습니다** — DB 와 예산 카운터가 팀에 하나뿐이라
  개발 PC 도 서버 것에 붙습니다. 그래서 `POSTGRES_PASSWORD` · `REDIS_PASSWORD` 는 필수이고,
  redis 는 `:?` 가드로 비밀번호 없이는 아예 뜨지 않게 되어 있습니다.

**profile 뒤** — 켜는 명령과 꺼 둔 이유가 각각 다릅니다.

| profile | 서비스 | 상태와 이유 |
| --- | --- | --- |
| `gait` | gait-analysis | 같은 방식. 가중치 2개를 `GAIT_RELEASE_DIR` 로 물립니다 (D-029) |
| `tools` | pgadmin | GUI 가 필요할 때만. 로그인 없는 모드라 띄워 둔 동안 누구나 접근 가능합니다 |

crawler-worker · crawler-beat 의 `crawler` profile 은 #65(코퍼스 서버 이관)가 뗐습니다 —
이제 기본 세트입니다.

파이썬 서비스 컨테이너는 전부 **같은 uv 베이스 이미지(`uv:1`)** 를 쓰고, 기동 시
`uv sync --frozen [--group/--extra ...] && exec uv run --no-sync ...` 한 가지 모양입니다.
소스는 이미지에 굽지 않고 바인드 마운트라, 코드 수정은 리로드로 반영되고 재시작이
필요한 것은 의존성이 바뀌었을 때뿐입니다.

venv 는 서비스마다 **별도 named volume** 입니다. 특히 backend(`--group ml --group screening`, torch 포함)와
크롤러는 볼륨을 합치면 크롤러의 exact `uv sync` 가 torch 를 지워 `/ask` 만 조용히 503 이
됩니다 — 볼륨 분리가 그 사고를 구조적으로 막는 장치입니다 (상세는 compose 의
crawler-worker 주석과 CLAUDE.md).

## 프론트 배포: releases + junction + PM2 reload

프론트만 컨테이너가 아닌 이유가 이 배포 방식입니다. `dev` 에 push/merge 되면 self-hosted
러너가 `deploy.yml` 을 실행합니다.

```
C:\deploy\daengs\
   releases\<커밋해시>-<실행번호>\   ← 배포마다 새 폴더 (standalone 빌드 결과)
   current  ──(junction)──▶ releases\...\
```

1. 필수 환경 변수 검증 — `GEMINI_API_KEY` 가 서버 `backend/.env` 에 없거나 비어 있으면
   컨테이너를 건드리기 전에 실패시키고, 이어 `docker compose config --quiet` 로 compose
   정의를 검증합니다.
2. `npm ci && npm run build` 후 standalone 산출물을 **새 릴리스 폴더**에 복사합니다.
   실행 중인 폴더와 빌드 폴더가 달라 Windows 파일 잠김(EBUSY)이 없습니다.
3. `current` junction 만 새 폴더로 갈아끼우고 `pm2 startOrReload` — cluster 모드라 워커를
   하나씩 교체해 무중단입니다. 폴더 이름에 실행번호까지 넣는 이유, junction 을
   `rmdir` 로만 지워야 하는 이유는 `ecosystem.config.js` · `deploy.yml` 주석에 있습니다.
4. `docker compose up -d` + nginx `-t` / reload — compose 정의와 마운트된 nginx 설정
   변경이 같은 배포에서 반영됩니다.
5. 스모크: Place 검색 실제 결과 · rate limit 429 · Journey 계약을 확인하고 실패하면
   배포를 실패로 처리합니다.

롤백은 재빌드 없이 `current` 를 이전 릴리스로 되돌리고 `pm2 reload` 하는 것입니다
(루트 README §롤백).

## 기동 순서와 복구

서버 PC 재부팅 후 자동으로 올라오는 것은 compose 서비스뿐입니다(`restart: unless-stopped`
+ Docker Desktop 자동 시작). **PM2 와 러너는 수동**이고, 순서가 중요합니다:

1. `pm2 resurrect` — PM2 데몬을 먼저.
2. 러너 `./run.cmd` — 나중에.

러너를 먼저 띄우면 배포 작업 안에서 PM2 데몬이 처음 생성되고, 작업이 끝날 때 데몬이
같이 종료되어 "배포는 성공했는데 서비스는 내려간" 상태가 됩니다. 이 절차를 일부러
자동화하지 않은 것까지 포함해 루트 README §서버 PC 재부팅 후가 원본입니다.

부분 장애의 모양을 알아두면 진단이 빠릅니다:

- `gait` profile 이 꺼져 있으면 **`/gait/*` 만 502**, 나머지는 멀쩡합니다.
- Screening 가중치가 없거나 손상되면 `/screen/v1/screen` 은 503 입니다. Skin 은 이제
  backend 와 프로세스를 공유하므로 backend 자체가 죽으면 로그인·`/ask` 를 포함한 main
  API 전체가 함께 영향을 받습니다 (D-040).
- backend 의 `ml` 그룹이 지워지면 **`/ask` 만 503**, 다른 API 는 멀쩡하고 로그도
  조용합니다 (CLAUDE.md 의 `uv sync` 함정).
- backend 재생성 직후 최대 10초는 nginx 가 옛 IP 로 갈 수 있습니다 (resolver `valid=10s`).
- 공개 `/training/chat` 은 하위 실패 종류와 무관하게 하위 호환용 503 응답을 유지합니다. 다만
  내부 `services/training_rag.py` 경계는 실제 생성 타임아웃을
  `TrainingRagTimeoutError` 로 보존하므로 오케스트레이션은 TIMEOUT 과 ERROR 를 구분합니다.

## 논리 오케스트레이션 — 직접 API 와 `/assistant/query`

여기서부터는 프로세스가 아니라 **요청의 종류**를 다룹니다. 위 물리 지도는 전부 CURRENT
사실입니다. 이 절 아래는 한때 TARGET(승인됐지만 미구현)이었던 것이 Card 1 → 2A → 2B →
3(PR #113 · #115)으로 실제 구현되어 지금은 **CURRENT / CONFIRMED** 입니다 — 그 목표가
무엇으로 승인됐는지의 기록은 지우지 않고, "아직 구현 안 됨"이라는 TARGET 표기만
갱신합니다. 아직 실제로 미구현인 것(en-US 로케일, Place/Journey 편입 등)은 각자의
자리에서 여전히 TARGET/FOLLOW-UP 으로 남습니다.

**CURRENT** — `backend/src/daengs_backend/orchestration/` 에 이미 만들어진 `RoutePlan` 을
소비하는 내부 LangGraph 실행 코어가 있습니다. Training·Life·Walk 어댑터, 순차 실행,
HANDOFF/CLARIFY 처리와 결정적 집계까지 구현됐습니다. Card 2B 로 production 의미 라우터도
같은 패키지에 들어왔습니다 — `semantic.py`(Gemini 의미 선택 + O-14 1회 재시도) ·
`planner.py`(결정적 신호 해소와 결정론적 RoutePlan 조립) · `service.py`(계획 → 기존 실행
코어 호출). Card 3 로 공개 `POST /assistant/query` 진입점도 붙었습니다 —
`routers/assistant.py`(인증·외부 DTO 검증·`PrincipalContext` 조립) ·
`schemas/assistant.py`(`extra="forbid"` 외부 요청 계약, `/walk` 과 같은 좌표 범위).
인증은 `/walk`·`/ask` 와 같은 `admin_or_app_user(Perm.READ)` 이고, 응답은
`AssistantResponse` 를 그대로 돌려줍니다 — 재해석하지 않습니다. 기존 직접 API 및
프론트 흐름은 바뀌지 않았습니다.

**v1 완료 체크포인트 (2026-09-01)** — 오케스트레이션 v1(Card 1 → 2A → 2B → 3)이 `dev` 에
merge 되어 있습니다.

- PR #113 — production 의미 라우터 (`semantic.py` · `planner.py` · `service.py`)
- PR #115 — 인증된 `POST /assistant/query` 진입점
- 최종 백엔드 흐름이 인증부터 능력 실행/HANDOFF·집계까지 실제로 연결돼 있습니다
- 포커스 E2E(실제 서비스/planner/그래프/집계, Gemini 전송·능력 어댑터만 대체) 통과
- 라이브 Gemini 의미 라우팅 스모크 통과 (프로덕션 경로 확인용 소규모 스모크 — Card 2A
  80건 벤치마크를 다시 도는 것이 아닙니다. 상세는 routing 문서 §4)
- 병합된 `dev` 상태 그대로에서 돌린 포커스 회귀 통과

검증 기준 커밋: `6227fddd58dab3bf6721eb6d1fca6111d9d1ad18` (Card 3 merge 직후 `dev`).
프론트를 `/assistant/query` 에 연결하는 작업과, 배포된 서버 인프라에서의 실제
Training/Life/Walk 능력 스모크는 이 문서가 다루는 오케스트레이션 구현의 범위 밖이며
아직 남은 별도 후속 작업입니다 — 오케스트레이션 자체가 미완성이라는 뜻이 아닙니다.

**CURRENT / CONFIRMED — v1 로 구현 완료** — 대화형 진입점 `/assistant/query` 하나를
두고, 그 뒤의 흐름 제어는 **LangGraph** 가 맡습니다. 아래 경계는 2026-08-30
어드버서리얼 아키텍처 리뷰(읽기 전용, `origin/dev` 코드 대조)를 거쳐 **사람이 최종
승인**했고(D-030~D-037 · orchestration-routing.md §6 의 결정 이력), Card 1~3 구현이
그대로 지킵니다.

- **LangGraph 는 오케스트레이터입니다** — 모든 결정을 쥐는 LLM 슈퍼바이저가 아닙니다.
  그래프는 라우팅·실행 순서·결과 수집이라는 흐름 제어만 소유합니다.
- **명시적 기능 UI 플로우는 기존 직접 API 를 그대로 씁니다.** 산책 기록 화면이 `/walk` 를
  부르는 것은 바뀌지 않습니다. `/assistant/query` 는 자연어·모호·다중 능력 요청 전용입니다.
- **인증은 그래프 밖입니다.** 기존 FastAPI 의존성 계층(D-015 · D-016)이 토큰을 검증하고,
  그래프는 **인증이 끝난 principal** 을 받아 능력별 **인가**만 판단합니다. 인가는 중앙
  매트릭스 한 곳이 정하며(D-036 · orchestration-routing.md §5), 토큰이 그래프 상태에
  들어가지 않습니다 (계약 불변식 — orchestration-contracts.md).
- **도메인 안전·거절 결정은 각 능력이 소유합니다.** 오케스트레이터는 상류의 REFUSED 를
  ERROR 로 재해석하지 않고, **자료 부족 기권(ABSTAINED)을 거절(REFUSED)로 접지도
  않습니다** (D-033). Training 의 SAFETY_REFUSAL/MEDICAL_REFUSAL 구분
  (docs/training/rag-demo.md · `schemas/training.py`)이 그대로 통과해야 합니다.
- **backend↔daengs_life 접점 규칙(D-018 의 "세 줄", `tests/test_main_stays_light.py` 로
  기계 강제)은 약화하거나 지우지 않습니다.** 오케스트레이션의 Life 어댑터가 **유일하게
  새로 승인된 접점**이고(D-035, O-11), 구현 시작 시 경계 테스트를 그 한 곳만 허용하도록
  갱신해 **다시 기계로 강제**합니다. 그 밖의 daengs_backend → daengs_life import 는
  여전히 금지입니다.
- **multipart 이미지·영상 워크플로는 전용 API 에 남습니다.** 대화로 "피부 사진 봐줘"가
  들어오면 실행이 아니라 해당 업로드/UI 플로우로 **HANDOFF** 합니다 (orchestration-routing.md).
- **능력별 생성 모델을 계약으로 통일하지 않습니다.** 지금은 Training(#93 이후
  `gemini-3.1-flash-lite`)과 Life 가 둘 다 Gemini 지만, 그것은 각 능력의 도메인 선택이
  우연히 겹친 것이지 공유 계약이 아닙니다 — 어느 쪽이 모델을 바꿔도 오케스트레이션은
  무관해야 합니다. 공유해야 하는 것은 모델 공급자가 아니라 **계약 · 안전 시맨틱 · 인가 ·
  라우팅 · 관측**입니다.
- **GraphRAG / Neo4j 는 폐기됐고 이 작업과 무관합니다.** LangGraph(흐름 제어 프레임워크)와
  GraphRAG(그래프 지식베이스)는 이름만 비슷한 남남입니다. 폐기된 산출물은 이관하지 않습니다.

**v1 범위 (CONFIRMED)** — EXECUTE 가능 능력은 **Training + Life + Walk** 셋입니다.
Skin·Gait 는 의미 라우터가 실제로 선택하는 **HANDOFF 대상**입니다(`semantic.py` 의
`handoffs.skin`/`handoffs.gait`, planner 의 고정 reason) — "아직 문서만 있고 라우터가
모르는 것"이 아니라, **EXECUTE 로는 절대 선택되지 않는다**는 뜻입니다 (§7).

**v1 LangGraph 프리미티브 (CONFIRMED)** — `StateGraph` · 일반 edge · 조건부 edge, 그리고
`Send` 는 동적 다중 능력 fan-out 이 **실제로 필요할 때만**. `Command` 는 나중 선택지.
서브그래프 · checkpointer · interrupt 는 v1 요구사항이 아닙니다.

## 능력 현실 · 준비도 (CURRENT — 2026-09-01, dev `6227fdd`(PR #115 merge) 기준)

능력들이 대칭이라고 가정하면 설계가 틀어집니다. 이 표가 **능력 준비도의 단일 원본**입니다
— 다른 문서는 여기로 링크하고 같은 표를 두 번 만들지 않습니다.

| 능력 | 현재 소스·런타임 가용성 | Card 1 오케스트레이션 역할 | 호출 형태 | 현재 기술 호출 가능? | 막는 것 · 비고 |
| --- | --- | --- | --- | --- | --- |
| **Training** | backend 프로세스 안 `daengs_training` 모듈 (#92·#93·#94·#112). `POST /training/chat`(관리자+SEARCH_INSPECT, #25·#30) → in-process `services/training_rag.py` → `RAGService.answer(top_k=4)`. 생성 Gemini `gemini-3.1-flash-lite`, 검색 E5 + 공용 pgvector 클러스터의 **`vectordb` DB**, `public.training_rag_documents`/`training_rag_chunks` 테이블 (#112, 아래 Training 토폴로지 절) | 실행 ✅ (assistant 경유는 앱 회원도 — D-036) | in-process — 어댑터는 `services/training_rag.py` 경계를 쓰고 `RAGService`·PGVector 내부로 직행하지 않습니다 | **예** | 안전 시맨틱은 상류 소유 — 공개 decision ANSWER·UNCERTAIN·SAFETY_REFUSAL·MEDICAL_REFUSAL (`schemas/training.py`, docs/training/rag-demo.md). 내부 경계가 실제 생성 타임아웃과 그 밖의 실패를 구분하며 공개 `/training/chat` 의 503 호환성은 유지합니다 (contracts §4) |
| **Life** | backend `POST /ask` — 같은 프로세스 안 (daengs_life, D-018 · D-021). 인증 앱 회원+관리자 (`admin_or_app_user(READ)`, main.py) | 실행 ✅ | in-process 어댑터 (D-035 — 기존 서비스 심 `daengs_life.app.services.ask`) | **예** | 기계 신호: 무근거 404 · 503(설정)/504(타임아웃)/502(상류) · `ungrounded` 품질 지표. **없는 것**: Training 급 안전 분류·산문 물러섬의 기계 신호 — 수용된 v1 한계 (D-035). 로드맵은 docs/life/roadmap.md 트랙 A·B |
| **Walk** | backend `/walk` — 같은 프로세스 안 (daengs_life.realtime). 인증 동일. 생성 없음 — **결정적** | 실행 ✅ | in-process 어댑터 (동일) | **예** | 판정은 자체 규칙 계층 소유 (RT-). **UNSAFE 는 성공한 도메인 판정**이지 거절이 아닙니다. 판정 불가 `unknown`(503+전체 본문)은 ABSTAINED 로 보존합니다 |
| **Skin** | 소스 `backend/src/daengs_screening/`, main backend 라우터 `POST /screen/v1/screen` (#100, D-040). 별도 서비스/profile 은 제거됐고 nginx 는 `/screen/*` 를 backend 로 전달합니다. screening lock 복구 완료 (#101). 가중치는 첫 요청에 지연 로딩 | **HANDOFF 만** | 전용 multipart 업로드 UI/API — 오케스트레이터가 실행하지 않음 | **예** — 가중치·의존성이 배포된 backend 에서 호출 가능 | 기술 가용성이 Card 1 범위를 넓히지 않습니다. PR #79 계약대로 `headline`·`body`·`action`·`disclaimer` 무수정 통과, top-1 병변명 없음(D-023), 이력은 저장소/이력 결정 뒤. 라우터는 현재도 인증·rate limit 이 없어 보안 후속은 별도 |
| **Gait** | 소스는 `backend/src/daengs_gait/` 와 shared lock으로 이관됐습니다 (#98, D-038). 런타임은 계속 별도 `gait-analysis` FastAPI/venv/볼륨이며 `gait` profile 로 기본 꺼짐 | **HANDOFF 만** | 전용 영상 업로드 UI/API — 오케스트레이터가 실행하지 않음 | **조건부** — profile·가중치를 갖추면 nginx `/gait/` 경유 호출 가능 | 소스 통합은 Card 1 편입이 아닙니다. 분 단위 영상 추론이라 동기 대화에 안 맞음 — 미래 도입 시 PENDING + job 메타데이터 경로 (orchestration-contracts.md) |
| **Place** | 소스 `backend/src/daengs_place/`, shared lock (#99, D-039). 런타임은 `place-search` 별도 FastAPI + 전용 PostGIS로 기본 기동. `/v2/places/*` 공개 API는 rate limit 적용 | v1 실행 대상 아님 — `handoffs[].target` 후보 (`place`, docs/life/roadmap.md §2 제안) | 별도 프로세스 직접 API | **예** | 소스/project 통합은 런타임 또는 Card 1 편입이 아닙니다. 장소 데이터는 Place 소유(D-026·D-039); 핸드오프 식별자 확정은 통합 카드의 사람 결정 |
| **Journey** | 소스 `backend/src/daengs_journey/`, shared lock (#99, D-039). 런타임은 `journey-service` 별도 FastAPI로 기본 기동, nginx `/journey` 유지 | v1 실행 대상 아님 | 별도 프로세스 직접 API | **예** | Place와 함께 소스가 이동했지만 기존 Usage Gate·프로세스 경계와 외부 계약은 유지. Card 1 실행 범위 확대 없음 |

Skin 이 main backend 에서 기술적으로 호출 가능해진 것은 **런타임 사실의 변화**이지
Card 1 역할의 변화가 아닙니다. multipart 이미지 획득과 통제 문구 보존이 필요한 전용
플로우라 대화 진입은 계속 HANDOFF 입니다. 인증·rate limit 이 없는 위험도 별도 컨테이너를
켜는 순간의 문제가 아니라 **현재 main backend 라우터의 보안 후속**으로 남습니다.
Gait 도 구현돼 있지만 profile·가중치가 필요하고 동기 대화 시간에 맞지 않아 HANDOFF 입니다.
미래에 실행할 때는 CapabilityResult 의 PENDING + job 메타데이터 경로를 씁니다. Place와
Journey 역시 기술적으로 호출 가능하지만 승인된 Card 1 실행 범위에는 들어오지 않습니다.

**Skin 결과를 다른 능력과 합성할 때도** (PR #79 의 2번 — 스크리닝 결과 + Life 제도 정보)
Skin 의 안전 통제 문구(`headline`·`body`·`action`·`disclaimer`)는 LLM 이 요약·재작성하지
않고 그대로 통과합니다 — 2단계 모델의 병변명 오답률(56.6%, D-023) 때문에 문구 계층이
지키는 방어를 합성 단계가 풀면 안 됩니다.

## Training 토폴로지 — 이관 완료, PGVector 는 본체 DB 로 통합 (CURRENT)

**CURRENT (2026-09-01, #112 반영)** — Training RAG 이관은 **완료됐습니다.** 소스는
`backend/src/daengs_training/` 모듈이고, backend 프로세스 안에서 in-process 로 돕니다
(#83 런타임 이행 → #92 PGVector pg18 → #93 생성 Gemini 전환 → #94 modular monolith
→ #105 PGVector 공용 클러스터 통합 → #112 `vectordb` 테이블 통합).
호스트 단독 FastAPI(`:8010`)·`DAENGS_TRAINING_RAG_BASE_URL`·backend→Training HTTP 홉은
더 이상 없습니다. 현재 호출 경로:

```
frontend → backend POST /training/chat
         → services/training_rag.py (asyncio.to_thread, lazy 싱글턴)
         → daengs_training.service.RAGService (top_k=4)
         → pgvector:5432/vectordb (본체와 같은 DB, public.training_rag_* 테이블) / Gemini API
```

**DB 토폴로지 (#112 — #105 의 전용 `dog_rag` DB 를 대체)** — Training 전용 `dog_rag`
데이터베이스와 전용 role 은 더 이상 production 대상이 아닙니다. Training 은 본체와
**같은 `vectordb` DB, 같은 접속 정보(`RAG_PGVECTOR_DSN` 기본값 = compose 의 본체
`POSTGRES_*`)** 를 쓰고, 소유 테이블로만 나눕니다:

```
공용 pgvector 컨테이너 / PostgreSQL 클러스터
└─ vectordb
   ├─ (본체 DAENGS 테이블)
   └─ public.training_rag_documents · public.training_rag_chunks   (Training 전용, #112)
```

스키마 원본은 여전히 `backend/infra/training_pgvector/schema.sql`(768차원)이고, 본체 DB
규칙과 같게 `db/init/05_training_rag.sql` 에도 같은 정의가 추가됐습니다(#112) — 빈
볼륨에서 새로 뜨면 Training 테이블까지 한 번에 만들어집니다. 더 이상 전용
`TRAINING_RAG_DB_PASSWORD`/`dog_rag` LOGIN role 이 없습니다.

**저장소·런타임 설정과 서버의 실제 상태는 다른 질문입니다.** 위 내용은 이 저장소의
코드·compose·`db/init/` 이 가리키는 대상이 `vectordb.training_rag_*` 라는 뜻입니다.
**이미 떠 있는 서버 DB** 에는 `db/init/` 이 적용되지 않으므로(볼륨이 빌 때만 실행),
`db/migrations/2026-09-01_training_rag_into_vectordb.sql` 을 배포 후 수동 적용해야
실제로 반영됩니다(본체 DB 규칙과 동일 — CLAUDE.md "이미 있는 DB를 바꾸는 SQL"). 옛
`dog_rag` DB 는 롤백 대비로 당분간 남아 있을 수 있지만 **더 이상 production 런타임
대상이 아닙니다.** 서버에 이 마이그레이션이 실제로 적용됐는지는 이 문서가 검증하지
않습니다 — 배포 확인이 필요한 별도 항목입니다.

E5 검색·evidence gate·의료 가드레일·Gemini 생성은 전부 `daengs_training` 이 소유하고,
`daengs_backend` 가 import 하는 것은 게이트웨이 한 곳뿐입니다
(`test_training_rag_monolith.py` 가 기계 강제 — main import 시 torch 비로딩 포함).

이 결과는 D-032 가 적어 둔 초기 TARGET("저장소 통합 ≠ 프로세스 통합, HTTP 경계 초기
유지")보다 한 걸음 더 간 것입니다 — #94 가 프로세스 통합(modular monolith)까지, #112 가
DB 통합까지 팀 승인으로 수행했고, 운영 실측에서 부담이 확인될 때만 서비스 분리를
재검토합니다(경계는 `services/training_rag.py` 한 곳이라 분리 전환이 어댑터 교체로
끝나는 성질은 유지됩니다). 서빙 계약 원본은 docs/training/rag-demo.md.

### 이관 출처와 경계 (CONFIRMED — 이관은 이 경계 안에서 수행됨)

검증된 Training 소스는 다음 한 지점이었고, 실제 이관(#83~#94)이 이 경계를 지켰습니다.

- commit `22495d28bc9a8869ba132d0b98206b10a9e8fbc3`
- tag `training-runtime-freeze-2026-08-30`
- R2 이관 판정: **CLEAR WITH RESTRICTION**

| 허용 | 금지 |
| --- | --- |
| freeze 태그의 clean checkout | dog-training-rag **Git 히스토리 반입** (subtree·히스토리 이관·브랜치 이관·fork 이식 전부) |
| 승인된 운영 서브셋의 **파일 단위 복사** | **raw/원문 코퍼스 커밋** |
| DAENGS_dev 안에서 **새로 만든 커밋** | 원문 텍스트가 든 과거 평가 스냅샷 이관 |
| | GraphRAG · Neo4j 산출물 (폐기됨) |

커밋되는 산출물에 **원문/청크 전문이 들어가지 않는다**는 불변식은 이관 후에도
유지/재도입합니다. 그리고 **백업 패키지 ≠ 배포 패키지**입니다 — 외부 전체 ZIP 은
개인 재해 복구 백업이고, 그것이 자동으로 운영 서버 코퍼스가 되지 않습니다. 공유/서버
인프라로의 코퍼스 배포는 미해결 소스들의 권리·출처 검증을 **따로** 통과해야 합니다.

### 서버 재구축 상태 (2026-09-01 갱신)

| 항목 | 상태 |
| --- | --- |
| 신규 서버 PGVector 재구축 | **완료** — 공용 pgvector 클러스터, 서빙 스코프 14문서/83청크 유지 (#92·#94 — 배포에서 재적재·재임베딩 안 함) |
| `vectordb.training_rag_*` 테이블 통합 (#112) | **저장소/compose 설정 완료** — `db/init/05_training_rag.sql`·`RAG_PGVECTOR_DSN` 은 `vectordb` 를 가리킴. **서버의 기존 DB 에 `db/migrations/2026-09-01_training_rag_into_vectordb.sql` 을 실제로 적용했는지는 배포 확인 필요** — 이 문서가 대신 검증하지 않습니다 |
| Training 포트 | **해소(무의미)** — 별도 프로세스가 없어 포트 자체가 사라짐 (#94) |
| monolith RSS · 첫 요청 지연 · 동시성 실측 | **FOLLOW-UP** — #94 가 merge blocker 로 두지 않고 운영 관찰 항목으로 넘김. 부담 확인 시에만 서비스 분리 재검토 |
| 운영 타임아웃 정합 (Gemini `GEMINI_TIMEOUT_MS` 등) | **FOLLOW-UP** |

## 프롬프트·로케일 정책 (미래 제약 — 런타임 무변경)

멘토 컨벤션은 **런타임 지시 프롬프트 = 영어 + Markdown** 입니다. 현재 Training 런타임
프롬프트 `grounded-answer-ko-v2`(`daengs_training/generation/gemini.py`)는 이 컨벤션을
아직 만족하지 않습니다 — #94 도 프롬프트 이행을 명시적으로 범위 밖에 뒀습니다.
프롬프트를 바꾸면 생성 모델의 출력 행동, 인용 행동, 근거 부족 자기보고 탐지, 평가와의
동등성이 전부 흔들리므로 별도 카드로만 합니다. 순서는 고정입니다:

```
별도 프롬프트 변경 카드 → 회귀 테스트 → 평가 재실행
```

> **사실 갱신 (2026-09-03, #161)** — 그 별도 카드가 `grounded-answer-ko-v3` 입니다. 모델
> 지시문은 영어, 출력 계약은 한국어("Answer the user in Korean." + 정본 근거 없음 문장
> `제공된 자료에는 이 질문에 대한 내용이 없습니다.` 그대로), 그리고 의미 변경은 하나 —
> **자료가 사용자가 실제로 말한 행동·문제를 직접 다루지 않으면 인접 문제로 답을 확장하지
> 않고 정본 문장만 낸다** (직접성 규칙). 서비스는 그 문장으로 시작하고 이어 쓰는 답도
> `model_reported_no_evidence` 로 UNCERTAIN 처리합니다. 검색·임계값·top_k·서빙 코퍼스는
> 그대로입니다. 회귀 테스트는 `tests/test_training_prompt_contract.py` ·
> `tests/test_training_service.py`; 동결 생성 평가는 저장소에 실행 가능한 형태로 없어
> 라이브 프로브 3건으로 대신했습니다 (#161 컨텍스트 메모).

로케일은 계약에 자리만 잡습니다: 지금은 `locale = "ko-KR"` 하나, 미래에 `"en-US"`
(orchestration-contracts.md). 영어 UI · 영어 코퍼스 · 영어 가드레일 행동은 이 문서
세트의 범위가 아니고 **지금 구현하지 않습니다.**

## 더 읽을 곳

| 주제 | 원본 |
| --- | --- |
| 각 컨테이너 정의와 함정 전부 | `docker-compose.yml` 주석 |
| 라우팅 규칙과 nginx 함정 | `nginx/default.conf` 주석 |
| 배포 단계 상세 | `.github/workflows/deploy.yml` · `ecosystem.config.js` |
| 운영 명령어 · 재부팅 절차 · 롤백 | 루트 `README.md` |
| 코드·환경 변수 규칙 | 루트 `CLAUDE.md` |
| 결정 배경 (D- / RAG- / RT-) | `docs/decisions.md` · `docs/life/decisions-rag.md` · `docs/life/decisions-realtime.md` |
| Training RAG 서빙 계약 | `docs/training/rag-demo.md` (색인: `docs/training/README.md`) |
| 생활 파트(Life·Walk) 로드맵 — 어댑터 준비 트랙 포함 | `docs/life/roadmap.md` |
| 오케스트레이터 공통 계약 (확정) | `docs/orchestration-contracts.md` |
| 라우팅 정책 · 인가 매트릭스 · 결정 이력 | `docs/orchestration-routing.md` |
