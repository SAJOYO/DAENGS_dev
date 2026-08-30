# 오케스트레이션 아키텍처

서비스가 늘면서 "무엇이 어디서 어떻게 뜨는지"가 docker-compose.yml 주석 · nginx 설정 ·
README · CLAUDE.md 에 흩어졌습니다. 이 문서는 그 전체 지도를 한 장으로 모은 것입니다.
개별 함정의 상세(왜 그 줄이 그렇게 생겼는지)는 각 파일의 주석이 원본이고, 여기서는
구조와 "왜 이렇게 나눴는지"만 다룹니다.

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
