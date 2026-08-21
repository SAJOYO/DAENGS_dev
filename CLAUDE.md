# DAENGS

Next.js 프론트엔드 + FastAPI 백엔드. 자체 서버(Windows PC)에 PM2 + nginx 로 배포합니다.

```
브라우저 :80 → nginx(도커) → host.docker.internal:3000 → PM2 (Next)
                           → host.docker.internal:8000 → FastAPI   (예정: nginx/default.conf 의 /api/ 블록 주석 해제)
```

## 폴더

| 경로 | 내용 |
| --- | --- |
| `frontend/` | Next.js 16 앱 (App Router, TypeScript, Tailwind 4) |
| `backend/` | FastAPI 앱, uv 로 관리 (Python 3.12). 패키지는 `src/daengs_backend/` |
| `nginx/default.conf` | 리버스 프록시 설정 |
| `docker-compose.yml` | nginx 컨테이너 |
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
uv add <패키지>            # 의존성 추가 (pip install 대신)
```

## 규칙

- **브랜치는 `dev` 가 기본입니다.** `dev` 에 push/merge 하면 self-hosted 러너가 자동 배포합니다.
  main 브랜치는 쓰지 않습니다.
- **백엔드 의존성은 반드시 `uv add` / `uv remove` 로.** `pyproject.toml` 을 직접 고치면
  `uv.lock` 과 어긋납니다. `uv.lock` 은 커밋합니다.
- **백엔드는 uv 기본 src 레이아웃**입니다. 코드는 `src/daengs_backend/` 안에 두고
  `from daengs_backend.config import settings` 처럼 패키지 이름으로 import 합니다.
  폴더 구분(MVC 등)은 아직 정하지 않았습니다 — 당분간 패키지 안에 평평하게 둡니다.
- **Python 은 3.12 로 고정**입니다 (`requires-python = ">=3.12,<3.13"`, `backend/.python-version`).
  로컬에 3.11 / 3.14 도 깔려 있으니 `uv run` 을 거쳐 실행하세요.
- **`frontend/AGENTS.md` 는 `next dev` 가 자동 생성/갱신합니다.** 지워도 다시 생기므로
  변경분이 보이면 그냥 같이 커밋하면 됩니다. `frontend/CLAUDE.md` 는 그 파일을 참조만 합니다.
- **CORS 는 로컬 개발용입니다.** 배포 환경에서는 nginx 가 `/api/` 를 같은 오리진으로
  프록시하므로 필요 없습니다. 오리진 추가는 `DAENGS_CORS_ORIGINS` 환경 변수로.
- 서버 PC 재부팅 후에는 PM2 와 러너를 **수동으로** 띄워야 합니다. 순서와 이유는
  루트 `README.md` 참고 (러너를 먼저 띄우면 배포 후 서비스가 내려갑니다).
