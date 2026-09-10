# DAENGS

Next.js 프론트엔드와 FastAPI 백엔드를 PM2 + nginx 로 자체 서버에 배포합니다.

```
daengs.~     :80   → nginx 컨테이너 → host.docker.internal:3000 → PM2 (Next, 호스트)
daengback.~  :8000 → nginx 컨테이너 → backend:8000              (기본 API 경로)
                                      → place-search:8000         (`/v2/places/`, `/territory/sites/`)
                                      → journey-service:8000      (`/journey`만)
```

## 요구 사항

- Node.js 20 이상
- Python 은 따로 설치하지 않아도 됩니다 — uv 가 3.12 를 받아 씁니다
- [uv](https://docs.astral.sh/uv/)
- Docker Desktop (**Linux 컨테이너 모드**)
- PM2 (`npm install -g pm2`)

## 로컬 개발 (개발 PC)

프론트엔드 → http://localhost:3000

```powershell
cd frontend
npm install
npm run dev
```

백엔드 → http://127.0.0.1:8000 (문서는 `/docs`)

```powershell
cd backend
Copy-Item .env.example .env
uv sync
uv run dev
```

의존성은 `uv add <패키지>` 로 추가합니다. `pyproject.toml` 을 직접 고치면 `uv.lock` 과 어긋납니다.

개발 PC 에서는 `backend/.env` 만 있으면 됩니다 (`backend/.env.example` 복사). 최상단
`.env` 는 compose(Postgres, pgAdmin) 용이라 **서버 PC 에서 compose 를 띄울 때만**
씁니다 — 개발 PC 에 만들 필요가 없습니다.

DB 는 팀에 하나뿐이라(서버에만 있음) `backend/.env` 의 `DAENGS_DB_HOST` 에 서버 IP를,
`DAENGS_DB_USER` / `DAENGS_DB_PASSWORD` 에 서버가 실제로 쓰는 계정을 넣어야 합니다.
암호화 키 3개(`DAENGS_JWE_KEY` 등)도 서버와 같은 값이어야 합니다 — 전부 팀 채널로
공유받으세요. 자세한 이유는 `backend/.env.example` 의 주석에 있습니다.

### 트레이스를 LangSmith 로 보기 (개발 PC 전용)

`/assistant/query` 한 요청의 라우팅 결정 → 검색 청크 → 프롬프트 → Gemini 답변을 한
트리로 봅니다 (`assistant_query` → `semantic_router` · `orchestration_engine` →
`training_rag` → `pgvector_search` · `gemini_generate`). 훈련 RAG 와 에이전트를 고치고
디버깅하는 자리입니다.

**운영 서버는 이 절과 무관합니다.** 서버의 트레이스는 우리 GCP Cloud Trace 로 가고
LangSmith 회사에는 아무것도 안 갑니다 (D-054). 이 절은 **개발자가 직접 치는 질문**을
**본인 계정**의 LangSmith 클라우드(무료 플랜, 월 5,000 트레이스)로 보내는 이야기입니다.
코드 변경은 없고 환경 변수뿐입니다. 실제 회원 계정으로는 테스트하지 마세요 —
회원 식별자·정밀 좌표는 마스킹되지만, 실사용 대화를 개인 계정으로 보낼 이유가 없습니다.

1. <https://smith.langchain.com> 가입 → Settings → API Keys → Personal Access Token.
2. `backend/.env` 에 `GEMINI_API_KEY` 와 `DAENGS_DB_*` 가 있어야 합니다 (위 문단).
3. **`uv sync --group ml`.** 훈련 RAG 의 검색기가 `sentence-transformers` 를 씁니다 —
   `ml` 없이 띄우면 `/life/ask` 만이 아니라 훈련 능력도 검색 단계에서 실패합니다.
4. 서버를 띄우는 **바로 그 PowerShell 창**에서 (`backend/` 에서):

   ```powershell
   $env:GEMINI_API_KEY = ((Get-Content .env | Select-String '^GEMINI_API_KEY=') -replace '^GEMINI_API_KEY=','')
   # 훈련 RAG 검색기의 DB 접속. backend/.env 의 DAENGS_DB_* 와 같은 값을 URL 한 줄로.
   $env:RAG_PGVECTOR_DSN = "postgresql://daengs:<비밀번호>@192.168.0.22:5432/vectordb"
   $env:LANGSMITH_TRACING="true"
   $env:LANGSMITH_TRACING_MODE="langsmith"
   $env:LANGSMITH_PROJECT="daengs-dev"
   $env:LANGSMITH_API_KEY="<본인 키>"
   uv run dev
   ```

   기동 로그에 `트레이싱을 켰습니다 — 모드=langsmith` 와 `⚠ 트레이스가 LangSmith(제3자)로
   나갑니다` 경고가 같이 뜹니다. 개발 PC 에서는 그 경고가 정상입니다.
5. 로그인해서 질문 하나를 보내면 LangSmith → Tracing → `daengs-dev` 프로젝트가
   자동으로 생기고 요청 하나가 한 줄입니다. 프로젝트는 손으로 만들 필요가 없습니다 —
   목록에 없다면 트레이스가 하나도 도착하지 않은 것입니다.

**함정 — 전부 2026-09-06 에 실제로 밟은 것입니다.**

- **`LANGSMITH_*` 를 `backend/.env` 에 적으면 조용히 안 켜집니다.** langsmith SDK 는
  `os.environ` 만 보고, `backend/.env` 는 pydantic-settings 가 자기 `Settings` 로만
  읽습니다. 셸 환경 변수로만 주세요. `DAENGS_` 접두사의 우리 설정을 새로 만들지 않는
  이유는 D-054 에 있습니다.
- **`GEMINI_API_KEY` 도 셸에 따로 올려야 합니다.** 시맨틱 라우터는 `backend/.env` 를
  읽지만, 훈련 RAG 의 생성부(`daengs_training/generation/gemini.py`)는 `os.environ` 만
  읽습니다. 서버에서는 compose 의 `env_file` 이 그 일을 하는데 개발 PC 에는 그 단계가
  없습니다. 빠뜨리면 라우팅·검색까지는 트레이스에 남고 생성만
  `GEMINI_API_KEY is required` 로 실패합니다.
- **`RAG_PGVECTOR_DSN` 도 셸에 줘야 합니다.** 훈련 RAG 의 검색기는 `DAENGS_DB_*` 를 안 보고
  이 변수 하나(`daengs_training/service.py`)를 읽으며, 없으면 `localhost:5432` 로 가서
  **260초를 기다린 뒤** `training_pgvector … ConnectionTimeout` 으로 실패합니다. 라우팅·
  임베딩까지는 되고 응답은 `FAILED` 라 트레이싱 문제처럼 보입니다 (2026-09-07 실측).
  서버에서는 compose 가 넣어 줍니다 (`docker-compose.yml` 의 `RAG_PGVECTOR_DSN`).
- **`uv run --env-file .env dev` 는 쓰지 마세요.** 얼핏 위 두 문제를 한 번에 푸는 것
  같지만, uv 의 dotenv 파서가 `DAENGS_KAKAO_APP_KEYS=["a","b"]` 안의 큰따옴표를 벗겨
  `[a,b]` 로 올리고, pydantic 이 목록 필드를 JSON 으로 읽다 `kakao_app_keys` 에서
  기동이 죽습니다.
- **팀 LAN 밖에서는 안 됩니다.** DB 와 Redis 는 `192.168.0.x` 에만 열려 있어서
  (CLAUDE.md), 다른 네트워크에서는 로그인부터 500 입니다. 트레이싱 설정 문제가 아닙니다.

## 배포

- `dev` 브랜치에 push/merge 하면 자동으로 배포됩니다.
- Actions 탭에서 수동 실행도 가능합니다.
- 배포는 **서버 PC 에 등록된 self-hosted 러너**가 수행하므로,
  러너가 실행 중이 아니면 작업이 대기 상태로 멈춥니다.

## 서버 PC 재부팅 후 (수동 실행)

재부팅하면 아래 둘은 **자동으로 올라오지 않습니다.**
서버 PC 에서 PowerShell 을 열어 직접 실행하세요. (의도적으로 자동화하지 않았습니다)

### 1. PM2 — 서비스 기동

```powershell
pm2 resurrect
```

`pm2 save` 로 저장해 둔 프로세스 목록을 복원합니다. 목록이 비어 있다면 직접 시작하세요.

```powershell
cd C:\IDE\actions-runner\_work\DAENGS_dev\DAENGS_dev
pm2 start ecosystem.config.js
```

### 2. GitHub Actions 러너 — 배포 대기

```powershell
cd C:\ide\actions-runner
./run.cmd
```

이 창을 닫으면 러너가 멈춰 배포가 되지 않습니다. 계속 열어 두세요.

> **순서를 지켜 주세요.** PM2 데몬을 러너보다 먼저 띄워야 합니다.
> 러너 안에서 PM2 데몬이 처음 생성되면, 배포 작업이 끝날 때 데몬이 함께 종료되어
> 배포는 성공했는데 서비스가 내려가 있는 상태가 됩니다.

compose 서비스들은 `restart: unless-stopped` 설정이라 Docker Desktop 이 시작되면 자동으로 살아납니다.

> **크롤러 워커·Beat(`crawler-worker` · `crawler-beat`)도 같이 살아납니다** (RAG-050).
> 따로 띄울 것은 없고, 확인만 하세요:
>
> ```powershell
> docker compose ps crawler-worker crawler-beat     # 둘 다 Up 이어야 합니다
> ```
>
> 워커가 **Restarting** 이면 코퍼스 폴더에 `manifests/crawl_log.jsonl` 이 없는 것입니다
> (일부러 안 뜨게 해 두었습니다 — 로그 없이 뜨면 전 소스를 다시 받습니다).
> 아래 "크롤러 · 코퍼스" 절의 "코퍼스가 없다" 를 보세요.

## 운영 명령어 (서버 PC)

아래 명령은 모두 서버 PC 에서 실행합니다.

### PM2

```powershell
pm2 list                  # 프로세스 목록
pm2 logs daengs-web       # 실시간 로그
pm2 monit                 # CPU / 메모리 대시보드
pm2 reload daengs-web     # 무중단 재시작
pm2 restart daengs-web    # 전부 내렸다 올림 (순간 끊김)
```

### 컨테이너

```powershell
docker compose up -d          # 전체 기동 / 설정 반영
docker compose ps             # 상태 확인
docker compose logs -f        # 로그
docker compose down           # 중지 (데이터는 남습니다)

docker compose up -d nginx    # 하나만 올리기
```

`nginx/default.conf` 를 수동으로 수정했다면 설정을 검사한 뒤 reload 하세요.

```powershell
docker compose exec -T nginx nginx -t
docker compose exec -T nginx nginx -s reload
```

자동 배포는 위 검사와 reload까지 실행합니다.

### backend

```powershell
docker compose logs -f backend      # 로그
docker compose restart backend      # 의존성(uv.lock)을 바꿨을 때
```

소스는 `backend/src` 를 마운트해서 씁니다. 파일을 고치면 컨테이너 안에서 자동으로
리로드되므로 재시작이 필요 없습니다. **의존성을 바꿨을 때만** 위 restart 를 실행하세요.
(`uv sync` 는 컨테이너가 뜰 때만 돕니다)

⚠ **Celery 워커(`gait-worker` · `territory-vision-worker`)는 리로드가 없고, 자동 배포도
`backend` 만 재시작합니다** (`deploy.yml`). 워커가 쓰는 코드(`services/gait.py` 등
`backend/src`)를 머지했으면 서버 PC 러너 체크아웃 폴더에서 직접:

```powershell
docker compose restart gait-worker territory-vision-worker
docker compose logs --tail 5 gait-worker      # `celery@… ready.` 가 새로 찍히면 반영
```

빠뜨리면 웹은 새 코드인데 워커는 옛 코드로 남아 **고친 버그가 그대로 재현**되고, 로그로는
구분이 안 됩니다 (2026-09-09, #355 에서 실제로 겪음 — 워커가 44시간 전 코드였습니다).

**`backend/.env` 를 고쳤을 때는 restart 로는 반영되지 않습니다.** `env_file` 값은 컨테이너를
만들 때 굳어지고, `restart` 는 그 컨테이너의 프로세스만 다시 띄웁니다. 컨테이너를 다시 만들어야
합니다 — 그런데 **`up -d` 를 손으로 돌리기 전에 셸에 `GEMINI_API_KEY` 를 올려야 합니다.**
compose 의 `environment: GEMINI_API_KEY: ${GEMINI_API_KEY:-}` 가 `env_file` 보다 우선하는데,
그 값은 최상단 `.env` 나 셸에서만 오기 때문입니다. 자동 배포는 `deploy.yml` 이 `backend/.env`
에서 읽어 셸에 올린 뒤 compose 를 돌려서 문제가 없지만, 사람이 그냥 `docker compose up -d backend`
를 치면 **빈 키가 박혀 의미 라우터가 죽습니다** (2026-09-07 실제로 그랬습니다 — `/assistant/query`
전부 FAILED). 러너 체크아웃에서:

```powershell
$line = Get-Content backend\.env | Where-Object { $_ -match '^\s*GEMINI_API_KEY\s*=' } | Select-Object -First 1
$env:GEMINI_API_KEY = ($line -replace '^\s*GEMINI_API_KEY\s*=\s*','').Trim().Trim('"').Trim("'")
docker compose up -d backend        # backend 만. --force-recreate 없이
docker compose exec backend python -c "import os; print(len(os.environ.get('GEMINI_API_KEY','')))"   # 0 이면 잘못된 것
```

**어느 폴더에서 돌리느냐도 중요합니다.** 컨테이너 이름이 고정돼 있어 서버의 다른 클론에서
`up -d` 를 돌려도 같은 운영 컨테이너를 잡고, **그 클론의 `backend/src` 가 마운트**됩니다. 반드시
러너 체크아웃(`C:\IDE\actions-runner\_work\DAENGS_dev\DAENGS_dev`, `dev`)에서 돌리세요.
`docker compose ls` 의 ConfigFiles 가 그 경로인지 먼저 보면 됩니다.

backend 컨테이너는 포트를 열지 않습니다. 바깥에서는 nginx 의 8000 을 통해서만 닿습니다.

### pgvector (PostgreSQL 18)

```powershell
docker compose exec pgvector psql -U postgres -d vectordb   # 콘솔
docker compose --profile tools up -d                        # pgAdmin 함께 (http://localhost:5050)
```

DB 접속 정보는 최상단 `.env` 로 지정합니다. `.env.example` 을 복사해서 쓰세요.

```powershell
Copy-Item .env.example .env
```

- `db/init/` 의 SQL(확장, 스키마, 트리거)은 **최초 기동 때 한 번만** 실행됩니다.
  이미 만들어진 볼륨에는 반영되지 않으니, 직접 psql 로 적용하세요.
  파일을 새로 추가했을 때(예: `03_auth.sql`)도 마찬가지입니다.

  ```powershell
  docker compose exec -T pgvector psql -v ON_ERROR_STOP=1 -U postgres -d vectordb `
      < db/init/03_auth.sql
  ```

- 인덱스는 `db/indexes.sql` 에 따로 있습니다. 데이터를 적재한 **뒤에** 수동으로 실행하세요.
- `db/init/` 은 **볼륨이 빌 때 한 번만** 돕니다. 이미 만들어진 DB 의 스키마를 바꾸는
  SQL 은 `db/migrations/` 에 있고, 배포한 뒤 직접 적용해야 합니다 (`db/migrations/README.md`).
- 데이터는 `pgdata` 볼륨에 있습니다. `docker compose down -v` 를 쓰면 **전부 지워집니다.**

### Place 검색

`backend/src/daengs_place`를 실행하는 `place-search`와 별도 PostGIS인 `place-db`는
기본 `docker compose up -d`에 포함됩니다.
기동 전에 기존 Alembic 이력이 자동 적용되며, 외부 요청은 nginx의
`POST /v2/places/search`와 `GET /territory/sites/nearby`로 받습니다. 시설 검색과 중립
점령지 게임판은 HTTP 계약을 섞지 않습니다. place-db 자체 포트는 호스트에 열지 않습니다.

점령지 140u 게임판 적재와 배포 확인은
[`docs/place/territory-sites.md`](docs/place/territory-sites.md)를 따릅니다.
자연어 Place 발견 기능의 운영 이주 경계와 단계별 PR 순서는
[`docs/place/discovery-migration.md`](docs/place/discovery-migration.md)에 있습니다.

```powershell
docker compose logs -f place-search
docker compose exec place-db psql -U place -d place
```

장소 DB 적재는 Actions의 **Place data sync**를 수동 실행합니다. `kcisa`는 공식
2025-03-24 CSV를 SHA-256으로 확인해 적재하므로 키 없이 실행할 수 있고 APP 기본 탭인
카페까지 채웁니다. `full`과 `incremental`은 최상단 `.env`의 두 공공데이터 키를 사용해
기존 MOIS·KTO 적재기를 실행합니다. 사용자 검색 중에는 외부 원천을 호출하지 않습니다.

```powershell
gh workflow run place-search-ingest.yml -f mode=kcisa
gh workflow run place-search-ingest.yml -f mode=full
gh workflow run place-search-ingest.yml -f mode=incremental
```

### Journey

장소 카드를 선택한 뒤 APP은 공개 `POST /journey`로 거리·시간의 실측/추정 상태와 지도 앱
handoff를 받습니다. `backend/src/daengs_journey`는 별도 컨테이너에서 실행되며 Place DB와
Dog Profile을 조회하지 않습니다. 원본
`DAENGS_geo main@c5f0d5f`의 현재 APP 좌표 요청만 처리합니다.

```powershell
docker compose logs -f journey-service
```

기본 route provider는 키 없는 `fake`라 결과가 `estimate`로 표시됩니다. TMAP 보행 실측을
사용할 때만 최상단 `.env`의 `JOURNEY_WALK_ROUTE_PROVIDER=tmap`,
`JOURNEY_USAGE_POLICY=dev`, `JOURNEY_TMAP_APP_KEY`를 설정합니다.

### 크롤러 · 코퍼스 (RAG-050)

워커(`daengs-crawler-worker`)와 Beat(`daengs-crawler-beat`)는 `docker compose up -d` 에 같이 뜹니다.
매일 KST 04:00 에 due 소스만 받고 **수집에서 멈춥니다** — 파싱·적재는 개발 PC 에서 사람이 합니다
(RAG-044 ⑤). 수동 트리거는 관리자 콘솔의 크롤 카드입니다 (RAG-047).
**GCP 에서는 크롤~적재를 Cloud Run 잡이 자동으로 합니다** — 여기와 별개의 사본이고 절차는
`docs/deploy/runbook.md` §6 "코퍼스 파이프라인 (GCP)" 입니다 (D-062 · `docs/deploy/corpus-pipeline.md`).

코퍼스(`raw/` + `manifests/crawl_log.jsonl`)는 최상단 `.env` 의 `DAENGS_CORPUS_DIR`
(`C:/deploy/daengs/corpus`) 에 있고, **이관 뒤로는 서버가 정본입니다.** 개발 PC 에서는 더 이상
`crawler run` 으로 공유 코퍼스(`data/`)를 채우지 않습니다 — 개발 중 수집은 임시
`DAENGS_DATA_DIR` 로 하고, 적재할 것은 아래 "서버 → 개발 PC" 로 가져옵니다.

```powershell
docker compose ps crawler-worker crawler-beat          # Up 이어야 함. 워커가 Restarting 이면 아래 "코퍼스가 없다"
docker compose logs -f crawler-worker                  # 04:00 발사 — "due 소스 N개 / 시드 30개 — …"
docker compose logs --since 24h crawler-beat | Select-String crawl-due   # Beat 가 실제로 쐈는지
docker compose exec crawler-worker uv run --no-sync python -m daengs_life.crawler due   # 받지 않고 판정만
docker compose exec redis redis-cli -a <REDIS_PASSWORD> LLEN celery     # 소비자 없는 프리페치 큐 길이 (아래 ⚠)
```

> ⚠ `LLEN celery` 는 **하루 1,440 씩 늘어나는 것이 정상**입니다. Beat 가 실시간 프리페치도 같이
> 쏘는데 그 소비자(RT-002 의 워커)를 아직 안 띄웠습니다 (RAG-050 ③). 1.5MB/일 남짓이라 당장은
> 두고, 그 워커가 뜨는 날 쌓인 것은 실행 없이 버려집니다 (`expires`). 수십만 단위로 보이면
> 그 카드를 앞당길 때입니다.

#### 최초 이관 (한 번) — **머지 전에** 합니다

머지가 곧 배포라, 코퍼스가 서버에 있어야 워커가 뜹니다. 없으면 워커만 재시작 루프에 빠지고
나머지 서비스는 멀쩡합니다 (아래 "코퍼스가 없다").

1. **개발 PC** — 옮기기 **직전에** 쌉니다. 공유 코퍼스는 여러 워크트리가 동시에 수집하므로
   어제 싼 zip 은 오늘 이미 낡았습니다.
   ```powershell
   uv run --no-project --python 3.12 python tools/corpus_pack.py --data-dir <메인 체크아웃>/data
   # → corpus-20260830-0412.zip   raw 648 files / 79.2 MB   crawl_log.jsonl 335 lines   (숫자를 적어 두세요)
   ```
2. zip 을 서버 PC 로 옮깁니다 — USB 나 팀 드라이브. **서버에는 SSH·SMB 가 없습니다**
   (열린 포트는 5432·6379 뿐).
3. **서버 PC** — 러너 체크아웃 **바깥**에 풉니다.
   ```powershell
   New-Item -ItemType Directory -Force C:/deploy/daengs/corpus | Out-Null
   Expand-Archive corpus-*.zip -DestinationPath C:/deploy/daengs/corpus -Force
   (Get-ChildItem C:/deploy/daengs/corpus/raw -Recurse -File).Count           # 1 의 raw 개수와 같아야
   (Get-Content C:/deploy/daengs/corpus/manifests/crawl_log.jsonl).Count      # 1 의 줄 수와 같아야
   ```
   `seed_sources.yaml` 은 여기 **없는 것이 맞습니다** — git 추적 파일이라 정본이 체크아웃이고,
   compose 가 체크아웃의 것을 `/data/manifests/` 위에 겹쳐 마운트합니다. 코퍼스 폴더에
   복사해 두면 배포가 갱신하는 시드와 갈라집니다.
4. 서버의 최상단 `.env`(러너 체크아웃 안) 에 한 줄 — `.env.example` 참고:
   ```
   DAENGS_CORPUS_DIR=C:/deploy/daengs/corpus
   ```
   없으면 `docker compose config` 단계에서 배포가 멈춥니다 (`:?` 가드).
5. 머지 → 배포가 `docker compose up -d` 로 워커·Beat 를 올립니다. 확인:
   ```powershell
   docker compose ps crawler-worker crawler-beat                                     # 둘 다 Up
   docker compose exec crawler-worker uv run --no-sync python -m daengs_life.crawler due
   # → "due N / 후보 12 / 시드 30" 에서 N 이 12 보다 작으면 로그가 같이 온 것입니다.
   #   N == 후보 전체면 로그가 안 온 것 — 04:00 전에 3 을 다시 하세요.
   docker compose logs backend | Select-String "임베딩 모델"     # /life/ask 가 여전히 200 인지 — venv 를 안 섞었다는 증거
   ```
6. 다음 날 04:00 을 넘긴 뒤 `docker compose logs --since 24h crawler-worker` 에서 발사와 결과를 봅니다.

#### 코퍼스가 없다 (워커가 Restarting)

```
코퍼스가 없습니다: /data/manifests/crawl_log.jsonl 이 없어 뜨지 않습니다. …
```

`DAENGS_CORPUS_DIR` 가 가리키는 폴더에 `manifests/crawl_log.jsonl` 이 없는 것입니다 —
경로 오타거나, 이관 전에 기동됐거나, 폴더를 새로 만들었거나. 위 3 을 하고
`docker compose up -d crawler-worker`. **빈 로그 파일을 만들어 넘기지 마세요** — 그러면 후보
전부를 다시 받아 개발 PC 와 다른 코퍼스가 됩니다. (정말로 빈 코퍼스에서 시작하려는 것이면
그것이 그 방법입니다.)

#### 서버 → 개발 PC (적재하러 가져올 때)

서버가 정본이므로 방향은 이쪽뿐입니다. 서버에는 파이썬이 없어 PowerShell 로 쌉니다.

```powershell
# 서버
Compress-Archive -Path C:/deploy/daengs/corpus/raw, C:/deploy/daengs/corpus/manifests -DestinationPath corpus-from-server.zip -Force
# 개발 PC — 메인 체크아웃의 data/ 에 덮어씁니다. 원본은 불변이라 같은 이름은 같은 내용이고,
# 로그는 서버 것이 상위 집합입니다 (개발 PC 가 공유 코퍼스에 더 안 받는다는 전제).
Expand-Archive corpus-from-server.zip -DestinationPath <메인 체크아웃>/data -Force
```

### 롤백

배포는 커밋 해시별 폴더에 쌓이고 `current` 링크가 그중 하나를 가리킵니다.
링크만 되돌리면 재빌드 없이 이전 버전으로 돌아갑니다.

```powershell
Get-ChildItem C:\deploy\daengs\releases      # 되돌릴 버전 확인

cmd /c rmdir "C:\deploy\daengs\current"
New-Item -ItemType Junction -Path "C:\deploy\daengs\current" `
         -Target "C:\deploy\daengs\releases\<커밋해시>"
pm2 reload daengs-web
```

## 프로젝트 구조

```
frontend/                 Next.js 앱
backend/                  Python 패키지·테스트·단일 pyproject/uv.lock (uv, Python 3.12)
  src/daengs_backend/     인증·회원·공통 FastAPI
  src/daengs_life/        생활비서·실시간 산책
  src/daengs_training/    훈련 RAG
  src/daengs_place/       Place 검색 API·적재기 (별도 컨테이너)
  src/daengs_journey/     단발 이동 스냅샷 (별도 컨테이너)
  src/daengs_screening/   피부 스크리닝 (main backend 의 /screen/* 에 등록)
  src/daengs_gait/        보행 영상 분석 (Celery gait-worker 컨테이너, profile: gait)
  src/daengs_walk/        산책 측정·공간 일기 조립 (DB/HTTP 를 모르는 측정 커널)
  src/daengs_evals/       평가·벤치마크 도구. 결과는 backend/evals/
  infra/place/            Place 전용 Alembic (별도 PostGIS)
  evals/                  평가·벤치마크 결과 데이터 (코드 아님)
  tools/                  단일 파일 일회성 스크립트만 (패키지 금지)
  gait_v4/                별도 uv 프로젝트 (의도된 예외, #304 뒤 정리)
nginx/default.conf        리버스 프록시 설정
docker-compose.yml        서버용 컨테이너 구성
docker/uv/Dockerfile      uv 를 얹은 공용 베이스 이미지 (uv:1)
db/init/                  DB 최초 기동 시 실행되는 SQL (확장 / 스키마 / 트리거)
db/migrations/            이미 돌고 있는 DB 에 손으로 적용하는 SQL
db/indexes.sql            인덱스. 적재 후 수동 실행
.env.example              환경 변수 서식 (최상단은 compose 용, backend/ 는 앱 용)
ecosystem.config.js       PM2 설정 (프론트)
docs/decisions.md         의사결정 기록
docs/collaboration.md     협업 규칙
.github/workflows/        배포 워크플로우
```
