# 오케스트레이션 아키텍처

서비스가 늘면서 "무엇이 어디서 어떻게 뜨는지"가 docker-compose.yml 주석 · nginx 설정 ·
README · CLAUDE.md 에 흩어졌습니다. 이 문서는 그 전체 지도를 한 장으로 모은 것입니다.
개별 함정의 상세(왜 그 줄이 그렇게 생겼는지)는 각 파일의 주석이 원본이고, 여기서는
구조와 "왜 이렇게 나눴는지"만 다룹니다.

문서는 셋으로 나뉩니다. **이 파일** = 물리 토폴로지(§1~§5, 전부 CURRENT 사실) +
논리 오케스트레이션 구조(§6~, CURRENT 와 TARGET 을 구분해 표기).
**[orchestration-contracts.md](orchestration-contracts.md)** = 오케스트레이터 공통 계약 제안.
**[orchestration-routing.md](orchestration-routing.md)** = 라우팅 정책과 미결 사항.

> 표기: **CURRENT** = 지금 사실 · **TARGET** = 승인된 목표 상태(아직 구현 안 됨) ·
> **CONFIRMED** = 확정된 설계 제약 · **OPEN** = 사람 결정 대기 · **PENDING** = 검증 대기 ·
> **FOLLOW-UP** = 별도 카드로 후속.

## 실행 주체는 넷입니다

서버 PC(Windows) 한 대 위에서 네 가지 방식으로 프로세스가 돕니다. 하나로 합치지 않은
것은 각각 이유가 있습니다.

| 주체 | 무엇을 띄우나 | 왜 여기인가 |
| --- | --- | --- |
| **Docker Compose** | nginx · backend · pgvector · redis · place-search · place-db (+ profile 뒤의 것들) | 리눅스 컨테이너로 통일된 런타임. `restart: unless-stopped` 라 Docker Desktop 이 뜨면 같이 살아납니다 |
| **PM2 (호스트)** | Next.js 프론트 (`daengs-web`, cluster ×2) | standalone 빌드를 releases 폴더로 무중단 교체하는 배포 방식(아래 §4)이 호스트 프로세스를 전제로 합니다 |
| **호스트 단독 프로세스** | Training RAG FastAPI (`:8010`) | DAENGS 저장소 바깥의 별도 서비스입니다. backend 가 `host.docker.internal:8010` 으로 호출만 합니다 (`docs/training-rag-demo.md`) |
| **self-hosted GitHub Actions 러너** | 배포 워크플로우 (`deploy.yml`) | 배포 대상이 이 PC 자신이라 러너도 이 PC 에 있습니다. 러너가 꺼져 있으면 배포는 대기 상태로 멈춥니다 |

크롤링(Celery worker · beat)은 Compose 안에 정의돼 있지만 `profiles: ["crawler"]` 뒤라
지금은 뜨지 않고, 개발 PC 에서 수동 실행합니다 (RAG-044, 루트 README 참고).

## 요청이 지나는 길

바깥에 열린 포트는 nginx 의 **80 과 8000 둘뿐**입니다. 백엔드 계열 컨테이너는 포트를
열지 않고(D-005), 새 서비스가 생겨도 포트를 늘리지 않고 8000 에 경로를 얹습니다 —
공유기 포트포워딩·방화벽·DNS 를 건드릴 일이 없고 HTTPS 도 나중에 한 번에 붙습니다 (D-024).

```
                     서버 PC (Windows)
                     ┌────────────────────────────────────────────────┐
 daengs.~    :80 ──▶ │ nginx(도커)                                     │
                     │   /            ─▶ host.docker.internal:3000 ───┼─▶ PM2: daengs-web (Next standalone ×2)
                     │   /api/*       ─▶ backend:8000  (접두사 제거)   │
                     │                                                │
 daengback.~ :8000 ─▶│   /v2/places/* ─▶ place-search:8000  (rate limit)
                     │   /journey     ─▶ journey-service:8000         │
                     │   /screen/*    ─▶ skin-screening:8000  (profile)
                     │   /gait/*      ─▶ gait-analysis:8000   (profile)
                     │   그 외        ─▶ backend:8000                 │
                     └────────────────────────────────────────────────┘
                          backend ─▶ pgvector:5432 · redis:6379
                                  ─▶ host.docker.internal:8010 (Training RAG, 호스트)
                          place-search ─▶ place-db:5432 (자기 전용 PostGIS)
```

경로별 규칙 요약 (원본과 상세 이유는 `nginx/default.conf` 주석):

| 진입 | 경로 | 대상 | 비고 |
| --- | --- | --- | --- |
| :80 | `/` | PM2 의 Next | upstream 블록 사용 — 게이트웨이 IP 라 재해석 불필요 |
| :80 | `/api/*` | backend | 접두사를 rewrite 로 뗍니다. 로그인(httpOnly 쿠키)의 전제인 같은-오리진 경로 (D-015) |
| :8000 | `/v2/places/*` | place-search | 공개 검색이라 IP 당 5r/s 제한 (D-028). 접두사 제거 없음 |
| :8000 | `/journey` | journey-service | URI·본문 무변환. Place 의 rate limit 을 여기로 넓히지 않습니다 |
| :8000 | `/screen/*` | skin-screening | profile 뒤 — 꺼져 있으면 이 경로만 502 |
| :8000 | `/gait/*` | gait-analysis | profile 뒤. 여기만 body 200m · timeout 600s (영상) |
| :8000 | 그 외 | backend | 접두사 제거 없음 |

컨테이너 대상 경로는 전부 **upstream 블록이 아니라 `set` 변수 + resolver** 입니다.
compose 가 컨테이너를 재생성하면 IP 가 바뀌는데, upstream 블록은 nginx 기동 때 한 번만
이름을 풀어서 재생성마다 502 가 났기 때문입니다 (#31). 새 경로를 추가할 때도 같은 모양
(`set` 이 `rewrite` 보다 먼저)을 지켜야 합니다.

## Compose 서비스 지도

`docker compose up -d` 로 뜨는 기본 세트와, profile 을 명시해야 뜨는 것들이 나뉩니다.

**기본 기동** — nginx · backend · pgvector · redis · place-search · place-db

- backend 는 pgvector·redis 의 **healthy 를 기다립니다.** redis 는 없어도 앱이 뜨지만,
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
| `screening` | skin-screening | 가중치(163/189MB)가 저장소에 없어 서버 디스크의 `SCREENING_RELEASE_DIR` 를 물려야 합니다 (D-022 · D-024) |
| `gait` | gait-analysis | 같은 방식. 가중치 2개를 `GAIT_RELEASE_DIR` 로 물립니다 (D-029) |
| `crawler` | crawler-worker · crawler-beat | **서버에 코퍼스(`data/`)가 없어서 꺼 둡니다.** 켜면 전 소스가 due 로 잡혀 개발 PC 와 별개의 코퍼스를 새로 만듭니다 (RAG-044). 코퍼스 이전 카드에서 profile 을 뗍니다 |
| `tools` | pgadmin | GUI 가 필요할 때만. 로그인 없는 모드라 띄워 둔 동안 누구나 접근 가능합니다 |

파이썬 서비스 컨테이너는 전부 **같은 uv 베이스 이미지(`uv:1`)** 를 쓰고, 기동 시
`uv sync --frozen [--group/--extra ...] && exec uv run --no-sync ...` 한 가지 모양입니다.
소스는 이미지에 굽지 않고 바인드 마운트라, 코드 수정은 리로드로 반영되고 재시작이
필요한 것은 의존성이 바뀌었을 때뿐입니다.

venv 는 서비스마다 **별도 named volume** 입니다. 특히 backend(`--group ml`, torch 포함)와
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

1. `docker compose config --quiet` — 필수 환경 변수(`DAENGS_TRAINING_RAG_BASE_URL` 등)가
   비어 있으면 컨테이너를 건드리기 전에 실패시킵니다.
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

Training RAG(호스트 `:8010`)도 재부팅 후 수동입니다 — 실행 절차는
`docs/training-rag-demo.md`, 컨테이너에서 닿으려면 `0.0.0.0:8010` 바인딩이어야 합니다.

부분 장애의 모양을 알아두면 진단이 빠릅니다:

- profile 서비스가 꺼져 있으면 **그 경로만 502**, 나머지는 멀쩡합니다.
- backend 의 `ml` 그룹이 지워지면 **`/ask` 만 503**, 다른 API 는 멀쩡하고 로그도
  조용합니다 (CLAUDE.md 의 `uv sync` 함정).
- backend 재생성 직후 최대 10초는 nginx 가 옛 IP 로 갈 수 있습니다 (resolver `valid=10s`).

## 논리 오케스트레이션 — 직접 API 와 `/assistant/query`

여기서부터는 프로세스가 아니라 **요청의 종류**를 다룹니다. 위 물리 지도는 전부 CURRENT
사실이고, 이 절부터는 CURRENT 와 TARGET 이 섞이므로 표기를 지킵니다.

**CURRENT** — 오케스트레이터는 없습니다. 모든 기능이 각자의 직접 API 이고, 어떤 능력을
부를지는 전부 프론트 UI 가 정합니다. 능력별 현실은 §7 의 표가 원본입니다.

**TARGET (CONFIRMED)** — 대화형 진입점 `/assistant/query` 를 하나 두고, 그 뒤의 흐름
제어를 **LangGraph** 가 맡습니다. 경계는 다음과 같습니다.

- **LangGraph 는 오케스트레이터입니다** — 모든 결정을 쥐는 LLM 슈퍼바이저가 아닙니다.
  그래프는 라우팅·실행 순서·결과 수집이라는 흐름 제어만 소유합니다.
- **명시적 기능 UI 플로우는 기존 직접 API 를 그대로 씁니다.** 산책 기록 화면이 `/walk` 를
  부르는 것은 바뀌지 않습니다. `/assistant/query` 는 자연어·모호·다중 능력 요청 전용입니다.
- **인증은 그래프 밖입니다.** 기존 FastAPI 의존성 계층(D-015 · D-016)이 토큰을 검증하고,
  그래프는 **인증이 끝난 principal** 을 받아 능력별 **인가**만 판단합니다. 토큰이 그래프
  상태에 들어가지 않습니다 (계약 불변식 — orchestration-contracts.md).
- **도메인 안전·거절 결정은 각 능력이 소유합니다.** 오케스트레이터는 상류의 REFUSED 를
  ERROR 나 "근거 부족"으로 재해석하지 않습니다. Training 의
  SAFETY_REFUSAL/MEDICAL_REFUSAL 구분(docs/training-rag-demo.md)이 그대로 통과해야 합니다.
- **multipart 이미지·영상 워크플로는 전용 API 에 남습니다.** 대화로 "피부 사진 봐줘"가
  들어오면 실행이 아니라 해당 업로드/UI 플로우로 **HANDOFF** 합니다 (orchestration-routing.md).
- **능력별 생성 모델을 통일하지 않습니다.** Training 은 gemma3:4b, Life 는 Gemini 인
  채로 갑니다. 공유해야 하는 것은 모델 공급자가 아니라 **계약 · 안전 시맨틱 · 인가 ·
  라우팅 · 관측**입니다.
- **GraphRAG / Neo4j 는 폐기됐고 이 작업과 무관합니다.** LangGraph(흐름 제어 프레임워크)와
  GraphRAG(그래프 지식베이스)는 이름만 비슷한 남남입니다. 폐기된 산출물은 이관하지 않습니다.

**v1 범위 (CONFIRMED)** — 실행 가능 능력은 **Training + Life + Walk** 셋입니다.
Skin·Gait 는 인터페이스/어댑터 **문서까지만** 두고 v1 실행 대상이 아닙니다 (§7).

**v1 LangGraph 프리미티브 (CONFIRMED)** — `StateGraph` · 일반 edge · 조건부 edge, 그리고
`Send` 는 동적 다중 능력 fan-out 이 **실제로 필요할 때만**. `Command` 는 나중 선택지.
서브그래프 · checkpointer · interrupt 는 v1 요구사항이 아닙니다.

## 능력 현실 (CURRENT)

능력들이 대칭이라고 가정하면 설계가 틀어집니다. 지금 실제 모습:

| 능력 | 경로 · 프로세스 | 생성/추론 | 거절·안전 시맨틱 | v1 오케스트레이션 |
| --- | --- | --- | --- | --- |
| **Training** | backend `POST /training/chat`(관리자 게이트, #30) → HTTP → 별도 Training RAG FastAPI (호스트 `:8010`, 개인 저장소) | Ollama **gemma3:4b** / 검색 intfloat/multilingual-e5-base + PGVector | **상류가 소유** — ANSWER·UNCERTAIN·SAFETY_REFUSAL·MEDICAL_REFUSAL (training-rag-demo.md) | 실행 대상 ✅ |
| **Life** | backend `POST /ask` — **같은 프로세스 안** (daengs_life.rag, D-018 · D-021) | **Gemini** / 상주 임베딩 | **동등한 거절 계약이 없음** — 아키텍처 관심사이지 문서에서 지어낼 것이 아님 (OPEN, orchestration-routing.md) | 실행 대상 ✅ |
| **Walk** | backend `/walk` — 같은 프로세스 안 (daengs_life.realtime) | 없음 — **결정적** | 자체 규칙 계층이 소유 (RT- 결정들) | 실행 대상 ✅ |
| **Skin** | nginx `/screen/` → skin-screening 컨테이너 — **profile 이라 기본 꺼짐** (D-024) | PyTorch 분류 | 인증 경계가 backend 와 **동등하지 않음** | 실행 대상 아님 — 어댑터 문서만 |
| **Gait** | nginx `/gait/` → gait-analysis 컨테이너 — **profile 이라 기본 꺼짐** (D-029) | 분 단위 영상 추론 | — | 동기 실행 대상 아님 — 미래 비동기/PENDING 시맨틱 후보 |

Skin·Gait 를 v1 에서 뺀 것은 미구현이라서가 아닙니다(둘 다 구현돼 있습니다).
배포가 꺼져 있고, 인증 경계가 다르고(Skin), 동기 대화 응답 시간에 안 맞아서(Gait)입니다.
Gait 가 들어올 때는 CapabilityResult 의 PENDING + job 메타데이터 경로(orchestration-contracts.md)를
씁니다 — 그 자리를 계약에 미리 잡아 두는 이유입니다.

## Training 토폴로지 — CURRENT vs TARGET

**CURRENT** — Training RAG 는 이 저장소 밖(개인 dog-training-rag 저장소)의 코드로,
서버에서는 호스트 단독 FastAPI(`:8010`)로 뜨고 backend 가 HTTP 로만 부릅니다 (§1 의 표).

**TARGET (CONFIRMED, 검증된 이관 후)** — 소스 코드가 DAENGS_dev 안의 전용 training-rag
유닛으로 들어옵니다. 단, **저장소 통합 ≠ 프로세스 통합**입니다:

- 런타임/프로세스는 daengs_backend 와 **계속 분리**됩니다.
- 기존 HTTP 능력 경계(`DAENGS_TRAINING_RAG_BASE_URL`)를 초기에는 그대로 유지합니다.
- 이 이관은 **아직 완료되지 않았습니다.** 완료된 것처럼 적힌 문서가 보이면 그 문서가 틀린 것입니다.

### 이관 출처와 경계 (CONFIRMED)

검증된 Training 소스는 다음 한 지점입니다.

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

### 서버 재구축 상태

아래는 **완료되지 않았습니다.** 월요일 서버 리허설 후 이 절을 갱신합니다.

| 항목 | 상태 |
| --- | --- |
| 신규 서버 PGVector 재구축 | **PENDING** |
| 서버 지연시간 검증 | **PENDING** |
| 최종 Training 포트 · 서버 GPU 동작 · Docker 리소스 제한 | **PENDING** |
| 운영 타임아웃 정합 (backend 의 read timeout 등) | **FOLLOW-UP** |

## 프롬프트·로케일 정책 (미래 제약 — 런타임 무변경)

멘토 컨벤션은 **런타임 지시 프롬프트 = 영어 + Markdown** 입니다. 현재 Training 런타임
프롬프트 `grounded-answer-ko-v2` 는 이 컨벤션을 아직 만족하지 않습니다. 그래도
**freeze/이관 중에는 다시 쓰지 않습니다** — 프롬프트를 바꾸면 gemma3:4b 의 출력 행동,
인용 행동, `model_reported_no_evidence` 탐지, 동결 평가와의 동등성이 전부 흔들려서
"이관이 잘 됐는지"를 잴 기준이 사라집니다. 순서는 고정입니다:

```
이관 동등성 확인 → 별도 프롬프트 변경 카드 → 회귀 테스트 → 동결 평가 재실행
```

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
| 결정 배경 (D- / RAG- / RT-) | `docs/decisions.md` · `docs/decisions-rag.md` · `docs/decisions-realtime.md` |
| Training RAG 통합 | `docs/training-rag-demo.md` |
| 오케스트레이터 공통 계약 (제안) | `docs/orchestration-contracts.md` |
| 라우팅 정책과 미결 사항 | `docs/orchestration-routing.md` |
