# DAENGS

Next.js 프론트엔드와 FastAPI 백엔드를 PM2 + nginx 로 자체 서버에 배포합니다.

```
daengs.~     :80   → nginx 컨테이너 → host.docker.internal:3000 → PM2 (Next, 호스트)
daengback.~  :8000 → nginx 컨테이너 → backend:8000              (기본 API 경로)
                                      → place-search:8000         (`/v2/places/`만)
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

> **크롤러 워커·Beat(`crawler-worker` · `crawler-beat`)는 지금 뜨지 않습니다.**
> `profiles: ["crawler"]` 라 `docker compose up -d` 에 안 걸립니다 (RAG-044). 서버에 코퍼스
> (`data/`)가 없어서 켜면 **전 소스가 due** 로 잡혀 개발 PC 와 별개의 코퍼스를 처음부터 새로
> 만듭니다. 코퍼스를 서버로 옮기는 카드에서 profile 을 떼고, 그때 이 절차에 재기동을 적습니다.
> 그 전까지 크롤은 개발 PC 에서 `python -m daengs_life.crawler run --source X` 로 합니다.

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

`place-search`와 별도 PostGIS인 `place-db`는 기본 `docker compose up -d`에 포함됩니다.
기동 전에 기존 Alembic 이력이 자동 적용되며, 외부 요청은 nginx의
`POST /v2/places/search`로만 받습니다. place-db 자체 포트는 호스트에 열지 않습니다.

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
handoff를 받습니다. 이 서비스는 Place DB와 Dog Profile을 조회하지 않으며, 원본
`DAENGS_geo main@c5f0d5f`의 현재 APP 좌표 요청만 처리합니다.

```powershell
docker compose logs -f journey-service
```

기본 route provider는 키 없는 `fake`라 결과가 `estimate`로 표시됩니다. TMAP 보행 실측을
사용할 때만 최상단 `.env`의 `JOURNEY_WALK_ROUTE_PROVIDER=tmap`,
`JOURNEY_USAGE_POLICY=dev`, `JOURNEY_TMAP_APP_KEY`를 설정합니다.

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
backend/                  FastAPI 앱 (uv, Python 3.12)
place-search/             Place 검색 API + Alembic (별도 PostGIS 사용)
journey-service/          장소 선택 뒤 단발 이동 스냅샷 + 지도 앱 handoff
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
