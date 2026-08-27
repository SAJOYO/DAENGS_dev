# DAENGS

Next.js 프론트엔드 + FastAPI 백엔드. 자체 서버(Windows PC)에 PM2 + nginx 로 배포합니다.

```
daengs.~     :80   → nginx(도커) → host.docker.internal:3000 → PM2 (Next, 호스트)
daengback.~  :8000 → nginx(도커) → backend:8000 (컴포즈 서비스, 컨테이너)
```

## 폴더

| 경로 | 내용 |
| --- | --- |
| `frontend/` | Next.js 16 앱 (App Router, TypeScript, Tailwind 4) |
| `backend/` | FastAPI 앱, uv 로 관리 (Python 3.12). 패키지는 `src/daengs_backend/` |
| `nginx/default.conf` | 리버스 프록시 설정 |
| `docker-compose.yml` | nginx + pgvector(PostgreSQL 18) + redis 컨테이너 |
| `docker/uv/Dockerfile` | uv 를 얹은 공용 베이스 이미지 (`uv:1`). backend 컨테이너가 씁니다 |
| `db/init/` | DB 최초 기동 때 한 번 실행되는 SQL (확장 / 스키마 / 트리거) |
| `db/migrations/` | **이미 돌고 있는 DB** 에 손으로 적용하는 SQL. 스키마를 바꾸면 `db/init/` 과 같이 고칩니다 |
| `db/indexes.sql` | 인덱스. 적재가 끝난 뒤 수동 실행 |
| `tools/` | 일회성 에셋·유틸 스크립트. `uv run --no-project` 로 돌린다 (전역 설치 없음) |
| `docs/decisions.md` | 의사결정 기록. 되돌리기 번거로운 결정은 여기에 |
| `docs/collaboration.md` | 협업 규칙. 우선순위 · Iteration · PR 기준 · 회고 |
| `ecosystem.config.js` | PM2 설정 (프론트) |
| `docs/` | 프로젝트 문서 |
| `.github/workflows/deploy.yml` | 배포 워크플로우 |

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
uv sync                    # .venv 동기화
uv run dev                 # 개발 서버 http://127.0.0.1:8000 (reload)
uv run run                 # 운영 서버 http://0.0.0.0:8000
uv run pytest              # 테스트 (backend/tests/)
uv add <패키지>            # 의존성 추가 (pip install 대신)
```

## 규칙

- **브랜치는 `dev` 가 기본입니다.** `dev` 에 push/merge 하면 self-hosted 러너가 자동 배포합니다.
  `main` 은 릴리즈 스냅샷입니다 — 완성 단위마다 `dev → main` PR 로 올리고, 작업은 하지 않습니다.
- **백엔드 의존성은 반드시 `uv add` / `uv remove` 로.** `pyproject.toml` 을 직접 고치면
  `uv.lock` 과 어긋납니다. `uv.lock` 은 커밋합니다.
- **백엔드는 uv 기본 src 레이아웃**입니다. 코드는 `src/daengs_backend/` 안에 두고
  `from daengs_backend.config import settings` 처럼 패키지 이름으로 import 합니다.
  패키지 안은 **MVC2 계층**으로 나눕니다 (D-011).
  `routers`=Controller / `services`=Service / `repositories`=DAO /
  `models`(SQLAlchemy)+`schemas`(Pydantic)=Model. View 는 Next.js 가 가져갑니다.
  각 폴더의 `__init__.py` 에 "무엇을 넣고 무엇을 넣지 말 것"이 적혀 있습니다.
  `src/` 밑에 패키지를 더 둘지(`daengs_rag` 등)는 **아직 정하지 않았습니다.**
- **DB 접근은 SQLAlchemy 2.0 async + asyncpg** 입니다 (D-011). 세션은
  `core.database.get_session` 의존성으로 받고, `commit` 은 services 계층에서 합니다.
  **스키마 원본은 `db/init/*.sql` 이고 Alembic 은 쓰지 않습니다** — `models/` 는 SQL 을
  따라가는 쪽이라, SQL 을 고쳤으면 모델도 손으로 맞춰야 합니다.
  **`db/init/` 은 볼륨이 빌 때만 실행되므로 서버 DB 에는 반영되지 않습니다.**
  이미 있는 DB 를 바꾸는 SQL 은 `db/migrations/` 에 파일로 남기고 배포 후 직접
  적용하세요. 버전 테이블이 없어 **무엇이 적용됐는지 DB 가 기억하지 않으니**,
  여러 번 돌려도 안전하게 쓰세요 (`IF NOT EXISTS` 등).
- **Python 은 3.12 로 고정**입니다 (`requires-python = ">=3.12,<3.13"`, `backend/.python-version`).
  로컬에 3.11 / 3.14 도 깔려 있으니 `uv run` 을 거쳐 실행하세요.
- **`frontend/AGENTS.md` 는 `next dev` 가 자동 생성/갱신합니다.** 지워도 다시 생기므로
  변경분이 보이면 그냥 같이 커밋하면 됩니다. `frontend/CLAUDE.md` 는 그 파일을 참조만 합니다.
- **backend 는 compose 로 띄우고 개발 모드로 돕니다.** `backend/src` 를 마운트해
  파일을 고치면 컨테이너가 리로드합니다. 재시작이 필요한 건 의존성을 바꿨을 때뿐이고,
  그때는 `docker compose restart backend` 를 직접 실행하세요 (워크플로우는 건드리지 않습니다).
- **backend 컨테이너는 포트를 열지 않습니다.** 바깥에서는 nginx 의 8000 을 통해서만 닿습니다.
  `daengs.~`(80) 는 프론트, `daengback.~`(8000) 는 API 입니다. 둘은 오리진이 달라
  CORS 가 필요합니다 — `DAENGS_CORS_ORIGINS` 에 넣는 값은 '부르는 쪽'인 프론트 도메인입니다.
- **DB 는 compose 로 띄웁니다.** `docker compose up -d` 는 nginx · pgvector · redis 를 함께 올립니다.
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
  (`POSTGRES_IP`). 개발 PC 에서 `docker compose up -d` 를 돌리면 nginx · pgvector · redis 가
  또 뜨면서 포트가 겹치고, 아무도 안 쓰는 빈 DB 가 생깁니다.
  개발 PC 에서는 `uv run dev` 로 앱만 띄우고 `DAENGS_DB_HOST` 가 서버 DB 를
  보게 하세요.
- **DB 포트는 일부러 LAN 에 열어 둡니다.** 같은 네트워크의 팀원이 붙어야 해서
  `0.0.0.0:5432` 바인딩을 유지합니다. 대신 `POSTGRES_PASSWORD` 를 `.env` 에서
  기본값이 아닌 값으로 지정하세요. pgAdmin(tools 프로파일)은 로그인 없는 모드라
  띄워 둔 동안에는 누구나 들어올 수 있습니다.
- **Redis 도 LAN 에 열어 둡니다** (D-019). 실시간 산책의 캐시이자 **일 예산 카운터**라,
  개발 PC 도 서버 Redis 에 붙어야 data.go.kr 의 1,000회/일 을 하나로 셉니다. Redis 는
  기본이 무인증이므로 최상단 `.env` 의 `REDIS_PASSWORD` 를 **반드시** 채우세요 — 비어
  있으면 `--requirepass ""` 가 되어 누구나 붙습니다. 접속은 `REDIS_URL` **한 줄**이라
  (`backend/.env`) 비밀번호에 `@` `/` `#` 이 들어가면 깨집니다. 영숫자로만 지으세요.
  **`maxmemory` 는 일부러 안 겁니다** — 나중에 Celery 워커가 같은 인스턴스를 쓰는데,
  eviction 은 DB 번호가 아니라 인스턴스 단위라 큐가 조용히 지워집니다.
- **DB collation 은 `C` 입니다** (의도한 설정). 한글끼리의 정렬은 C 에서도 정확하고,
  `LIKE` 인덱스와 비교 속도에서 유리합니다. 영문 대소문자나 한글·영문 혼합 정렬이
  필요한 쿼리에서만 `ORDER BY x COLLATE "ko-KR-x-icu"` 를 붙이세요.
  DB 기본값을 바꾸려면 볼륨을 지우고 다시 만들어야 합니다.
- **환경 변수 파일은 두 개입니다.** 최상단 `.env` 는 compose(Postgres, pgAdmin) 용,
  `backend/.env` 는 앱 용입니다. 각각 옆에 `.env.example` 이 있습니다.
  `backend/.env` 의 `DAENGS_DB_*` 는 **개발 PC 에서 `uv run dev` 로 띄울 때** 쓰는
  값입니다. 서버 컨테이너에서는 compose 의 `environment` 가 같은 이름으로 덮어써서
  최상단 `.env` 의 `POSTGRES_*` 를 넘깁니다 (D-009).
  **접속 정보는 URL 한 줄이 아니라 조각으로 받습니다** — `db_host` / `db_port` /
  `db_user` / `db_password` / `db_name` 을 `config.py` 가 `URL.create` 로 조립합니다.
  이어 붙이지 않는 이유는 비밀번호의 특수문자 때문입니다 (D-013).
  옛 `DAENGS_DATABASE_URL` 이 `.env` 에 남아 있으면 backend 가 뜨지 않고 알려 줍니다.
- **암호화 키 3개는 기본값이 없습니다** (`DAENGS_JWE_KEY` `DAENGS_AES_KEY`
  `DAENGS_BLIND_INDEX_KEY`). 없으면 backend 가 아예 뜨지 않습니다 — 만드는 법은
  `backend/.env.example` 에 있습니다. 개인정보 암복호화는 `core/crypto.py`,
  관리자 비밀번호는 `core/password.py` 를 쓰고, 둘을 바꿔 쓰지 마세요 (D-012).
  **셋 다 개발 PC 전부와 서버가 같은 값을 씁니다** — DB 가 팀에 하나뿐이라, 키가
  다르면 한쪽이 암호화·조회한 데이터를 다른 쪽이 못 읽습니다. git 에는 올리지 않고
  팀 채널로 공유하세요. **AES 키를 잃으면 암호문을 영영 못 엽니다** — 어딘가 백업해
  두세요.
- **CORS 는 지금 배포 환경에도 필요합니다.** `daengs.~`(80) 와 `daengback.~`(8000) 는
  오리진이 달라서입니다. 로그인 API 카드에서 `nginx/default.conf` 의 `/api/` 블록
  주석을 열어 같은 오리진으로 묶을 예정이고(httpOnly 쿠키가 가려면 필요합니다),
  그 전까지는 오리진 추가를 `DAENGS_CORS_ORIGINS` 환경 변수로 하세요.
- 서버 PC 재부팅 후에는 PM2 와 러너를 **수동으로** 띄워야 합니다. 순서와 이유는
  루트 `README.md` 참고 (러너를 먼저 띄우면 배포 후 서비스가 내려갑니다).
- **협업 규칙은 `docs/collaboration.md` 에 있습니다.** 우선순위(P0~P3) · Iteration 기간 · Hold 판단은
  사람이 정합니다. Claude 는 제안까지만 하고, 작업 단위는 PR 본문의 `## 작업 목록` 을 기준으로 합니다.
