# DAENGS

Next.js 프론트엔드 + FastAPI 백엔드. 자체 서버(Windows PC)에 PM2 + nginx 로 배포합니다.

```
daengs.~     :80 ─┐                ┌─ nginx:80   → host.docker.internal:3000 → PM2 (Next, 호스트)
                  ├─ 호스트명으로 갈림 ─┤
daengback.~  :80 ─┘                └─ nginx:8000 → backend:8000 (기본 API 경로)
                                                 → place-search:8000 (`/v2/places/`, `/territory/sites/`)
                                                 → journey-service:8000 (`/journey`만)
```

**바깥에서는 둘 다 `:80` 입니다.** 8000 은 **LAN 안에서만** 열립니다
(`192.168.0.22:8000` 은 되고 공인 IP 의 8000 은 timeout — 2026-09-04 실측).
`docker-compose.yml` 이 `8000:8000` 을 매핑하는 것은 그래서 LAN 용입니다.

**호스트명을 갈라 주는 것은 이 저장소에 없습니다.** `nginx/default.conf` 의 두
`server` 블록은 **둘 다 `server_name _`(catch-all)** 이라 nginx 혼자서는 호스트명으로
못 가릅니다 — 실제로 LAN nginx 의 80 은 Host 를 `daengback.~` 로 바꿔 넣어도 Next 가
답합니다. 그런데 공개로는 `daengback.~:80` 이 API 를 줍니다. **공인 IP 와 이 nginx
사이에 호스트명으로 갈라 주는 무언가가 하나 더 있고, 그 설정은 저장소 밖에 있습니다.**
무엇인지는 아직 안 적혀 있으니, 이 경로를 만질 일이 생기면 **서버를 직접 보고 확인하세요.**

## 폴더

| 경로 | 내용 |
| --- | --- |
| `frontend/` | Next.js 16 앱 (App Router, TypeScript, Tailwind 4) |
| `backend/` | 팀 Python 프로젝트, uv 로 관리 (Python 3.12). `src/`의 backend·life·training·place·journey·**screening**·**gait** 패키지와 단일 `pyproject.toml`·`uv.lock`을 가집니다 — D-039 · D-040 · D-038 |
| `backend/src/daengs_gait/` | 강아지 보행 영상 분석 (PyTorch/ultralytics). 코드는 backend 프로젝트에 있고 Celery `gait-worker` 컨테이너에서만 실행됩니다 — D-038(D-029 의 소스 배치만 대체, runtime isolation 은 유지). 옛 `gait-analysis` HTTP 서비스는 D-063 4단계에서 제거됐고 nginx `/gait/` 는 410 묘비만 남습니다. compose `profile: gait` 라 **기본으로는 안 뜹니다.** 가중치는 저장소에 없습니다 |
| `backend/src/daengs_place/` | Place 검색과 중립 점령지 읽기 (FastAPI + PostGIS). 코드는 backend의 단일 Python 프로젝트에 있고 `place-search` 컨테이너로 따로 실행됩니다. nginx `/v2/places/`·`/territory/sites/`, 자기 DB(place-db)·Alembic(`backend/infra/place/`)을 가지며 backend·Dog Profile과 독립입니다 — D-026, D-027, D-039. 원본·소유권은 `docs/place/UPSTREAM.md` |
| `backend/src/daengs_journey/` | 장소 선택 뒤 단발 경로 스냅샷. 코드는 backend 프로젝트에 있고 `journey-service` 컨테이너로 따로 실행됩니다. nginx `/journey`로 공개되며 Place DB·Dog Profile과 독립입니다. 원본·범위는 `docs/journey/UPSTREAM.md` — D-039 |
| `backend/src/daengs_evals/` | 재사용되는 평가·벤치마크 도구(`answer_quality`·`router_benchmark`·`orchestrator_comparison`·`training_quality`·`place_fixtures`). `uv run python -m daengs_evals.<pkg>…` 로 부릅니다. 결과는 `backend/evals/` 에 쌓입니다 |
| `backend/evals/` | 위 도구가 읽고 쓰는 결과·골드 데이터(jsonl/json/md). 코드가 아니라 사람이 검토하는 산출물입니다. 상세는 `backend/evals/README.md` |
| `backend/tools/` | 단일 파일 일회성 스크립트만 둡니다 — 패키지는 만들지 않습니다. `uv run python tools/x.py` 로 부릅니다. 루트 `tools/` 와 달리 backend 의존성(venv)을 그대로 씁니다 |
| `backend/gait_v4/` | 별도 uv 프로젝트입니다 — 의도된 예외이고, 이유는 `backend/gait_v4/DAENGS-NOTE.md`. #304 뒤에 정리합니다 |
| `nginx/default.conf` | 리버스 프록시 설정 |
| `docker-compose.yml` | nginx + backend + pgvector + redis + place-search + place-db + 크롤러 워커·Beat 컨테이너 |
| `docker/uv/Dockerfile` | uv 를 얹은 공용 베이스 이미지 (`uv:1`). Python 서비스 컨테이너가 씁니다 |
| `db/init/` | DB 최초 기동 때 한 번 실행되는 SQL (확장 / 스키마 / 트리거) |
| `db/migrations/` | **이미 돌고 있는 DB** 에 손으로 적용하는 SQL. 스키마를 바꾸면 `db/init/` 과 같이 고칩니다 |
| `db/indexes.sql` | 인덱스. 적재가 끝난 뒤 수동 실행 |
| `tools/` | 일회성 에셋·유틸 스크립트. `uv run --no-project` 로 돌린다 (전역 설치 없음) |
| `docs/decisions.md` | 의사결정 기록. 되돌리기 번거로운 결정은 여기에 |
| `docs/collaboration.md` | 협업 규칙. 우선순위 · Iteration · PR 기준 · 회고 |
| `ecosystem.config.js` | PM2 설정 (프론트) |
| `docs/` | 프로젝트 문서 |
| `.github/workflows/deploy.yml` | 배포 워크플로우 |
| `.github/workflows/backend-tests.yml` | **모든 PR 에서 `uv run pytest` 전체.** `paths` 필터가 없는 것이 의도입니다 — 필터에 안 걸려 검사가 안 돌던 것이 이 파일이 생긴 이유입니다 (#230). `ml`·`gait`·`screening` 그룹은 안 깔고, 그 테스트는 skip 됩니다 |
| `.github/workflows/{journey,place-search}-tests.yml` | 그 두 서비스 전용. pytest 는 위와 겹치지만 **compose 렌더·nginx 문법 검사·패키지별 ruff** 를 들고 있어 남겨 둡니다 |

도감(네오 채소 홀로그램 카드)은 **이 저장소에 없습니다.** `SAJOYO/DAENGS_CARDS` 로
나가서 GitHub Pages 로 뜹니다 — <https://cards.weareithero.cloud/> (D-025).
랜딩(`frontend/app/page.tsx`)의 카드는 그 주소를 가리키는 외부 링크입니다.

## 명령어

프론트엔드 (`frontend/`):

```powershell
npm install
npm run dev        # http://localhost:3000
npm run build
npm run lint
```

백엔드 (`backend/`):

```powershell
uv sync --extra place      # 전체 로컬 테스트용 .venv 동기화 (Place 전용 의존성 포함)
uv run check               # 저장소 규칙 검사 (약 3초) — 머지 전에 무조건
uv run dev                 # 개발 서버 http://127.0.0.1:8000 (reload)
uv run run                 # 운영 서버 http://0.0.0.0:8000
uv run pytest              # 테스트 전체 (backend/tests/, 약 9분)
uv run pytest tests/place  # Place 테스트만
uv run pytest tests/journey # Journey 테스트만
uv add <패키지>            # 의존성 추가 (pip install 대신)
```

## 규칙

- **브랜치는 `dev` 가 기본입니다.** `dev` 에 push/merge 하면 self-hosted 러너가 자동 배포합니다.
  `main` 은 릴리즈 스냅샷입니다 — 완성 단위마다 `dev → main` PR 로 올리고, 작업은 하지 않습니다.
- **머지 전에 `uv run check`. 백엔드를 건드렸으면 `uv run pytest` 까지.**
  `dev` 머지가 곧 배포라 그 둘이 사실상 마지막 관문입니다. `check` 는 **3초**이고
  (`pytest` 는 약 9분), 봐 주는 자리가 서로 다릅니다 — `pytest` 는 `testpaths = ["tests"]` 라
  `backend/tests/` 만 보고, 마이그레이션 규칙 체커는 저장소 루트 `tools/` 에 있어서
  **`pytest` 를 아무리 돌려도 한 번도 안 돕니다.** 2026-09 에 CI 가 그 자리에서만 12건을
  잡았는데, 그때는 로컬에서 재현할 방법 자체가 없었습니다.
  검사 목록은 `daengs_backend/cli/check.py` 한 곳에 있고 `uv run pytest` 도 같은 것을
  부릅니다(`tests/test_repo_checks.py`) — **거기에 느린 것을 넣지 마세요.** 3초라서 매번
  돌리는 것이고, `pytest` 와 묶는 순간 이쪽까지 같이 안 돌게 됩니다.
  `sql` 모드만 예외로 CI 에 남아 있습니다 (`psql` 과 버리는 로컬 Postgres 가 필요합니다).
- **백엔드 의존성은 반드시 `uv add` / `uv remove` 로.** `pyproject.toml` 을 직접 고치면
  `uv.lock` 과 어긋납니다. `uv.lock` 은 커밋합니다.
- **Python 코드는 uv 기본 src 레이아웃**입니다. 팀 소유 패키지는 `backend/src/daengs_*`에 두고
  `from daengs_backend.config import settings` 처럼 패키지 이름으로 import 합니다.
  일반명 `app` 패키지를 만들지 않습니다. `daengs_backend` 안은 **MVC2 계층**으로 나눕니다 (D-011).
  `routers`=Controller / `services`=Service / `repositories`=DAO /
  `models`(SQLAlchemy)+`schemas`(Pydantic)=Model. View 는 Next.js 가 가져갑니다.
  각 폴더의 `__init__.py` 에 "무엇을 넣고 무엇을 넣지 말 것"이 적혀 있습니다.
  코드 위치와 프로세스 경계는 별개입니다. `daengs_place`와 `daengs_journey`는 같은 lock을
  쓰지만 별도 컨테이너·별도 venv 볼륨으로 실행합니다 (D-039).
- **DB 접근은 SQLAlchemy 2.0 async + asyncpg** 입니다 (D-011). 세션은
  `core.database.get_session` 의존성으로 받고, `commit` 은 services 계층에서 합니다.
  **기본 DB의 스키마 원본은 `db/init/*.sql` 이고 Alembic 은 쓰지 않습니다** — `models/` 는 SQL 을
  따라가는 쪽이라, SQL 을 고쳤으면 모델도 손으로 맞춰야 합니다.
  **`db/init/` 은 볼륨이 빌 때만 실행되므로 서버 DB 에는 반영되지 않습니다.**
  이미 있는 DB 를 바꾸는 SQL 은 `db/migrations/` 에 파일로 남기고 배포 후 직접
  적용하세요. 버전 테이블이 없어 **무엇이 적용됐는지 DB 가 기억하지 않으니**,
  여러 번 돌려도 안전하게 쓰세요 (`IF NOT EXISTS` 등).
  예외로 별도 PostGIS인 place-db는 이관해 온 리비전 역사를 유지하므로
  `backend/infra/place/alembic/`이 스키마 원본입니다 (D-026, D-039).
- **Python 은 3.12 로 고정**입니다 (`requires-python = ">=3.12,<3.13"`, `backend/.python-version`).
  로컬에 3.11 / 3.14 도 깔려 있으니 `uv run` 을 거쳐 실행하세요.
- **`frontend/AGENTS.md` 는 `next dev` 가 자동 생성/갱신합니다.** 지워도 다시 생기므로
  변경분이 보이면 그냥 같이 커밋하면 됩니다. `frontend/CLAUDE.md` 는 그 파일을 참조만 합니다.
- **Python 서비스는 compose에서 `backend/src`와 단일 lock을 공유합니다.** backend는 개발 모드로 돕니다. 로컬에서
  `backend/src` 파일 하나를 고치면 컨테이너가 리로드합니다. 다만 배포 checkout이 Windows bind mount 아래 파일을
  한꺼번에 교체할 때는 polling이 변경을 놓칠 수 있어, 자동 배포가 `backend`를 명시적으로 재시작합니다.
  의존성을 바꿨을 때는 영향받는 `backend`·`place-search`·`journey-service`를 재생성하세요.
- **`/life/ask` 의 임베딩 모델은 backend 프로세스에 상주합니다** (D-021). 그래서 컨테이너의
  `command` 가 `uv sync --frozen --group ml && uv run --no-sync dev` 입니다.
  ⚠ **컨테이너 안에서 `uv sync` 를 인자 없이 돌리지 마세요** — 그건 exact 동기화라 `ml` 을
  지웁니다(`uv run` 은 inexact 라 안 지웁니다. uv 0.12.5 실측). 그러면 torch 가 빠져
  `/life/ask` 만 503 이 되는데 다른 API 는 멀쩡해서 로그에 아무 문제도 안 보입니다.
  고칠 때는 `uv sync --group ml` 로 부르세요.
  상시 비용은 **RAM 약 2.4GB** 이고, 그것이 서버 여유를 위협하면 그때 별도 프로세스로 뗍니다
  (D-021 의 재개 조건 ⓐ~ⓓ). **개발 PC 는 `uv sync` 만 해도 backend 가 뜹니다** — `ml` 이
  없으면 `/life/ask` 가 503 이고, **훈련 능력도 검색 단계에서 실패합니다**
  (`daengs_training/retrieval/pgvector.py` 가 sentence-transformers 를 씁니다). 라우팅·
  트레이스까지 보려면 `uv sync --group ml`. 예열은 `DAENGS_WARM_UP_ENCODER=false` 로 끌 수
  있습니다.
- **서빙 임베딩 모델과 코퍼스가 어긋나면 조용히 틀립니다.** 문서 벡터와 질의 벡터가 다른
  모델이면 코사인이 무의미해지는데 **차원이 같아서(1024) 예외가 하나도 안 납니다.**
  `EMBEDDING_MODEL_KEY` 를 바꿨으면 `rag load --model` 로 다시 적재하세요. 기동 로그의
  `임베딩 모델 불일치` 경고가 그것을 알려 줍니다.
- **`daengs_backend` 가 `daengs_life` 를 부르는 접점은 `main.py` 의 세 줄뿐입니다** —
  등록 두 줄(`/life/walk-conditions` · `/life/ask`)과 예열 한 줄. 그 이상으로 늘리지 마세요. D-021 의 2단계
  (`/life/ask` 를 별도 프로세스로)가 싼 이유가 그 접점의 크기입니다. 특히 `rag` 가 읽는
  `POSTGRES_*` 를 `DAENGS_DB_*` 로 통일하고 싶어지는 자리에서 통일하면 나중에 되돌립니다.
- **backend 컨테이너는 포트를 열지 않습니다.** 바깥에서는 nginx 를 통해서만 닿습니다.
  `daengs.~` 는 프론트, `daengback.~` 는 API 이고 **둘 다 공개 포트는 80 입니다**
  (nginx 안에서 80 / 8000 두 블록으로 갈리지만, 그건 서버 안쪽 이야기입니다 — 위 그림).
  둘은 오리진이 달라 CORS 가 필요합니다. **포트가 아니라 호스트명이 달라서**입니다 —
  `DAENGS_CORS_ORIGINS` 에 넣는 값은 '부르는 쪽'인 프론트 도메인입니다.
- **DB 는 compose 로 띄웁니다.** `docker compose up -d` 는 nginx · backend · pgvector · redis와
  Place 검색(place-search · place-db), 크롤러 워커·Beat(crawler-worker · crawler-beat)를 함께 올립니다.
  접속 정보는 최상단 `.env`. `db/init/` 은 최초 1회만 실행되므로,
  이미 만들어진 볼륨에는 반영되지 않습니다.
- **`POSTGRES_USER` · `POSTGRES_PASSWORD` · `POSTGRES_DB` 도 볼륨이 빌 때만 반영됩니다.**
  `db/init/` 과 같습니다. 이미 돌고 있는 DB 에서 이 값을 바꾸면 컨테이너만 새 값을 쓰고
  DB 안의 계정은 그대로라, backend 가 `password authentication failed` 로 죽습니다
  (없는 롤이든 비밀번호가 틀렸든 메시지가 같습니다). 계정을 바꾸려면 psql 로 직접 하세요:

  ```powershell
  docker compose exec -it pgvector psql -U postgres
  ```
  ```
  \du                 -- 볼륨에 실제로 있는 롤 확인
  \password <계정>     -- 비밀번호 변경 (화면·히스토리·로그에 안 남습니다)
  ```

  롤을 새로 만들었으면 `ALTER DEFAULT PRIVILEGES` 까지 걸어 두세요. 안 그러면
  **나중에 다른 계정으로 만든 테이블이 앱 계정에 안 보입니다.**
- **compose 는 서버 PC 에서만 띄웁니다.** DB 는 팀에 하나뿐이고 서버 PC 에 있습니다
  (`POSTGRES_IP`). 개발 PC 에서 `docker compose up -d` 를 돌리면 서버용 컨테이너들이
  또 뜨면서 포트가 겹치고, 아무도 안 쓰는 빈 DB들이 생깁니다.
  개발 PC 에서는 `uv run dev` 로 앱만 띄우고 `DAENGS_DB_HOST` 가 서버 DB 를
  보게 하세요.
- **크롤은 서버가 합니다** (RAG-050). compose 의 워커·Beat 가 매일 KST 04:00 에 due 소스만 받고
  **수집에서 멈춥니다** — 파싱·청킹·임베딩·적재는 개발 PC 에서 사람이 합니다 (RAG-044 ⑤).
  코퍼스(`raw/` + `manifests/crawl_log.jsonl`)는 서버 디스크의 `DAENGS_CORPUS_DIR`(최상단 `.env`,
  러너 체크아웃 밖)에 있고 **서버가 정본**입니다. 개발 PC 에서 `crawler run` 으로 공유 `data/` 를
  채우지 마세요 — 서버와 갈라지고 `data/` 는 git 미추적이라 아무도 알려주지 않습니다. 개발 중
  수집은 임시 `DAENGS_DATA_DIR` 로, 적재할 원본은 서버에서 가져옵니다 (루트 `README.md`
  "크롤러 · 코퍼스"). 워커는 로그 파일이 없으면 **일부러 뜨지 않습니다** — 로그 없이 뜨면 전 소스가
  due 로 잡혀 다른 코퍼스를 만들기 때문입니다.

  **GCP 는 다릅니다** (D-062, 2026-09-08 ~ 11-17 실험). 거기서는 Cloud Run 잡 `corpus-refresh` 가
  크롤부터 적재까지 사람 없이 돌고, 코퍼스는 GCS 버킷 `daengs-corpus` 의 **별도 사본**입니다
  (초기 사본의 출처는 집 서버가 아니라 **개발 PC 의 `raw/`** 입니다 — GCP DB 가 그것에서 나왔기
  때문입니다). 집 서버 정본과는 갈라져 있고 합치지 않습니다. GCP 의 `documents` 를 손으로 갈아
  끼우지 마세요. 절차와 **처음 굽고 돌리며 걸린 것 열 가지**는 `docs/deploy/runbook.md` §6
  "코퍼스 파이프라인 (GCP)" 에 있습니다 — 이미지를 만질 일이 생기면 그것부터 읽으세요
  (CUDA torch 가 조용히 CPU 로 깔리는 것, GPU 잡 타임아웃 1h 상한, Git Bash 의 경로 변환 등).
  ⚠ **파이프라인 이미지에는 `pdf` 그룹(PyMuPDF)이 들어갑니다** — AGPL 격리(RAG-032 ②)가 말하는
  오프라인 쪽이 이 잡이라 맞습니다. **서빙 backend 이미지에는 여전히 넣지 마세요.**
- **DB 포트는 일부러 LAN 에 열어 둡니다.** 같은 네트워크의 팀원이 붙어야 해서
  `0.0.0.0:5432` 바인딩을 유지합니다. 대신 `POSTGRES_PASSWORD` 를 `.env` 에서
  기본값이 아닌 값으로 지정하세요. pgAdmin(tools 프로파일)은 로그인 없는 모드라
  띄워 둔 동안에는 누구나 들어올 수 있습니다.
- **Redis 도 LAN 에 열어 둡니다** (D-019). 실시간 산책의 캐시이자 **일 예산 카운터**라,
  개발 PC 도 서버 Redis 에 붙어야 data.go.kr 의 1,000회/일 을 하나로 셉니다. Redis 는
  기본이 무인증이므로 최상단 `.env` 의 `REDIS_PASSWORD` 를 **반드시** 채우세요 — 비어
  있으면 `docker compose` 가 아예 멈춥니다 (`:?` 가드). 접속은 `REDIS_URL` **한 줄**이라
  (`backend/.env`) 비밀번호에 `@` `/` `#` 이 들어가면 깨집니다. 영숫자로만 지으세요.
  **`maxmemory` 는 일부러 안 겁니다** — 나중에 Celery 워커가 같은 인스턴스를 쓰는데,
  eviction 은 DB 번호가 아니라 인스턴스 단위라 큐가 조용히 지워집니다.
- **DB collation 은 `C` 입니다** (의도한 설정). 한글끼리의 정렬은 C 에서도 정확하고,
  `LIKE` 인덱스와 비교 속도에서 유리합니다. 영문 대소문자나 한글·영문 혼합 정렬이
  필요한 쿼리에서만 `ORDER BY x COLLATE "ko-KR-x-icu"` 를 붙이세요.
  DB 기본값을 바꾸려면 볼륨을 지우고 다시 만들어야 합니다.
- **워크트리에서 작업해도 `data/` 는 한 곳에 쌓으세요.** `data/` 는 git 미추적이라(RAG-017)
  워크트리마다 따로 생기고 **워크트리를 지우면 코퍼스가 같이 지워집니다.** 실제로 parsed
  250건(조례 208 · 보조금24 37 · 운송 5)을 그렇게 잃었고, 어느 PC 에도 없어 재수집으로만
  복구됩니다 — 개정되는 원문은 재수집이 곧 다른 코퍼스라 그건 복구가 아닙니다.
  `backend/.env` 의 `DAENGS_DATA_DIR` 을 메인 체크아웃의 절대 경로로 고정하면
  어느 워크트리에서 돌리든 한 곳을 봅니다. 새 워크트리에 `.env` 를 복사할 때
  그 줄이 같이 갑니다. 그 `data/` 에서 개발 PC 가 가진 정본은 **`processed/` 뿐**입니다 —
  `raw/` 와 로그의 정본은 서버입니다 (위 "크롤은 서버가 합니다").
- **환경 변수 파일은 두 개입니다.** 최상단 `.env` 는 compose(Postgres, pgAdmin) 용,
  `backend/.env` 는 앱 용입니다. 각각 옆에 `.env.example` 이 있습니다.
  `backend/.env` 의 `DAENGS_DB_*` 는 **개발 PC 에서 `uv run dev` 로 띄울 때** 쓰는
  값입니다. 서버 컨테이너에서는 compose 의 `environment` 가 같은 이름으로 덮어써서
  최상단 `.env` 의 `POSTGRES_*` 를 넘깁니다 (D-009).
  **접속 정보는 URL 한 줄이 아니라 조각으로 받습니다** — `db_host` / `db_port` /
  `db_user` / `db_password` / `db_name` 을 `config.py` 가 `URL.create` 로 조립합니다.
  이어 붙이지 않는 이유는 비밀번호의 특수문자 때문입니다 (D-013).
  옛 `DAENGS_DATABASE_URL` 이 `.env` 에 남아 있으면 backend 가 뜨지 않고 알려 줍니다.
  **서버의 `backend/.env` 를 고쳤으면 `docker compose restart` 로는 반영되지 않습니다** —
  `env_file` 은 컨테이너를 만들 때 굳습니다. `docker compose up -d backend` 로 다시 만들되,
  그 전에 **셸에 `GEMINI_API_KEY` 를 올려야 합니다**: compose 의 `environment:` 가 `env_file` 보다
  우선하고 `${GEMINI_API_KEY:-}` 는 셸/최상단 `.env` 에서만 오므로, 빈 셸에서 `up -d` 를 치면 빈 키가
  박혀 의미 라우터가 죽습니다 (2026-09-07 실측). 자동 배포는 `deploy.yml` 이 그 변수를 올려 줍니다.
  절차는 루트 `README.md` "backend" 절.
- **암호화 키 3개는 기본값이 없습니다** (`DAENGS_JWE_KEY` `DAENGS_AES_KEY`
  `DAENGS_BLIND_INDEX_KEY`). 없으면 backend 가 아예 뜨지 않습니다 — 만드는 법은
  `backend/.env.example` 에 있습니다. 개인정보 암복호화는 `core/crypto.py`,
  관리자 비밀번호는 `core/password.py` 를 쓰고, 둘을 바꿔 쓰지 마세요 (D-012).
  **셋 다 개발 PC 전부와 서버가 같은 값을 씁니다** — DB 가 팀에 하나뿐이라, 키가
  다르면 한쪽이 암호화·조회한 데이터를 다른 쪽이 못 읽습니다. git 에는 올리지 않고
  팀 채널로 공유하세요. **AES 키를 잃으면 암호문을 영영 못 엽니다** — 어딘가 백업해
  두세요.
- **CORS 는 지금 배포 환경에도 필요합니다.** `daengs.~` 와 `daengback.~` 는 오리진이
  달라서인데, **포트는 둘 다 80 이라 같습니다 — 다른 것은 호스트명뿐입니다.**
  브라우저에게는 그것으로 충분히 다른 오리진이라, "포트를 맞추면 CORS 가 필요 없다"는
  성립하지 않습니다 (2026-09-04 에 문서가 `:8000` 이라 그렇게 읽혔습니다).
  같은 오리진으로 묶는 답은 포트가 아니라 **경로**입니다 — `nginx/default.conf` 의
  `/api/` 블록이 이미 그것을 하고 있고(`daengs.~/api/...` → backend), 프론트는
  `lib/api.ts` 에서 `/api/...` 만 부릅니다. httpOnly 쿠키가 가려면 그 길이라야 합니다.
  그 밖의 클라이언트(앱 등)가 쓰는 오리진 추가는 `DAENGS_CORS_ORIGINS` 환경 변수로 하세요.
- 서버 PC 재부팅 후에는 PM2 와 러너를 **수동으로** 띄워야 합니다. 순서와 이유는
  루트 `README.md` 참고 (러너를 먼저 띄우면 배포 후 서비스가 내려갑니다).
- **협업 규칙은 `docs/collaboration.md` 에 있습니다.** 우선순위(P0~P3) · Iteration 기간 · Hold 판단은
  사람이 정합니다. Claude 는 제안까지만 하고, 작업 단위는 PR 본문의 `## 작업 목록` 을 기준으로 합니다.
