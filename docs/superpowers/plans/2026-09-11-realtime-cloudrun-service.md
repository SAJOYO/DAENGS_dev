# 실시간 산책·날씨 Cloud Run 서비스 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `daengs_life/realtime/`(산책 적합도 · 과거 날씨)을 Cloud Run 서비스 `daengs-realtime` 으로 떼어, GCP 에서 min-instances=0 으로 돌린다. 개발서버는 안 바꾼다.

**Architecture:** 코드는 제자리에 두고 **이미지만** 다르게 굽는다(안 1). 상태(Redis)는 옮기지 않고 VM 의 `10.178.0.2:6379` 를 Direct VPC egress 로 쓴다(안 A). 인증은 Cloud Run 에 IAM 만 걸고 `daengs_backend` 가 게이트웨이가 된다(ⓐ). `DAENGS_REALTIME_URL` 이 비면 지금과 똑같은 in-process 경로이고, 값이 있으면 HTTP 다 — **되돌리기가 변수 하나다.**

**Tech Stack:** Python 3.12 · uv (`--only-group realtime`) · FastAPI/uvicorn · Cloud Run (service) · Direct VPC egress · Secret Manager · Artifact Registry + Cloud Build

**Spec:** [`docs/deploy/realtime-service.md`](../../deploy/realtime-service.md) (운영 설계) · [`docs/superpowers/specs/2026-09-11-realtime-cloudrun-design.md`](../specs/2026-09-11-realtime-cloudrun-design.md) (기각 근거) · 결정 `docs/decisions.md` **D-070** · 카드 **#435**

## Global Constraints

- Python 은 **3.12 고정** (`requires-python = ">=3.12,<3.13"`). 실행은 항상 `uv run` 을 거친다.
- 의존성은 **`uv add` / `uv remove`** 로만 넣는다. `pyproject.toml` 직접 수정 금지 (CLAUDE.md). `uv.lock` 은 커밋한다.
- **머지 전 `uv run check`** (3초). 백엔드를 건드렸으므로 `uv run pytest` 도 (약 9분).
- **파일 읽기·쓰기·수정은 Read / Edit / Write 도구로.** heredoc·sed 로 파일을 만들지 않는다.
- 커밋 메시지 끝에:
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>` / `Claude-Session: https://claude.ai/code/session_01BRhehE5W6rbzgFFvCzRZju`
- **작업 브랜치는 `docs/realtime-cloudrun-design`** (#435). 새로 파지 않는다.
- GCP 고정값: 프로젝트 `daengs` · 리전 `asia-northeast3` · VM 사설 IP `10.178.0.2` · 서브넷 `default` (`10.178.0.0/20`) · SA `corpus-pipeline@daengs.iam.gserviceaccount.com` · Artifact Registry `asia-northeast3-docker.pkg.dev/daengs/daengs`
- **`gcloud` 는 PowerShell 로 부른다.** Git Bash 는 `/opt/...` 같은 컨테이너 경로를 `C:/Program Files/Git/opt/...` 로 바꾸고, `MSYS_NO_PATHCONV=1` 은 gcloud 자체를 깨뜨린다 (둘 다 2026-09-11 실측). PowerShell 에서도 **native 명령에 넘기는 따옴표가 사라지므로** 값은 인자로 넘긴다.
- **8초 · 30초 예산은 이 작업에서 바꾸지 않는다** (D-070 §8).

## 이미 확인된 전제 (2026-09-11 실측 — 다시 재지 않는다)

| | 결과 |
| --- | --- |
| Cloud Run **잡**이 `10.178.0.2:6379` 에 닿나 | ✅ `b'-NOAUTH Authentication required.\r\n'` — 방화벽 규칙을 **안 만들었는데도** 통했다. `default-allow-internal` 이 이미 덮는다 |
| Cloud Run **서비스**가 Direct VPC egress 를 받나 | ✅ `--network=default --subnet=default --vpc-egress=private-ranges-only` 로 배포·기동 확인 |

→ **`allow-redis-from-run` 방화벽 규칙은 필요 없다.** 설계 문서(§4)의 그 줄을 Task 6 에서 정정한다.

---

## File Structure

| 파일 | 책임 |
| --- | --- |
| `backend/src/daengs_life/app/realtime_main.py` | **새로 만듦.** realtime 전용 ASGI 앱. 라우터 둘 + 캐시 lifespan. RAG 를 모른다 |
| `backend/pyproject.toml` `[dependency-groups] realtime` | **새로 만듦.** realtime 이 쓰는 여섯 |
| `docker/realtime/Dockerfile` · `Dockerfile.dockerignore` | **새로 만듦.** 의존성 + 코드를 굽는다 |
| `backend/src/daengs_backend/config.py` | `realtime_url` 한 줄 추가 |
| `backend/src/daengs_backend/services/realtime_client.py` | **새로 만듦.** ID 토큰 + httpx. 응답을 **손대지 않고** 돌려준다 |
| `backend/src/daengs_backend/routers/life_walk.py` | **새로 만듦.** `/life/walk-conditions` 프록시. 인증은 등록 시점 |
| `backend/src/daengs_backend/main.py` | 등록을 갈림길로 |
| `backend/src/daengs_backend/orchestration/adapters/life.py` | `_walk_life` · `_weather_at_life` 를 갈림길로 |
| `infra/gcp/realtime.sh` · `infra/gcp/realtime-teardown.sh` | **새로 만듦.** 배포·삭제 |
| `backend/tests/test_realtime_service_split.py` | **새로 만듦.** 앱 모양 · 갈림길 · 503 보존 |

---

## Task 1: realtime 전용 앱과 의존성 그룹

**Files:**
- Create: `backend/src/daengs_life/app/realtime_main.py`
- Modify: `backend/pyproject.toml` (`uv add --group realtime` 로), `backend/uv.lock`
- Test: `backend/tests/test_realtime_service_split.py` (신규)

**Interfaces:**
- Produces: `daengs_life.app.realtime_main:app` (FastAPI 인스턴스) · `create_app() -> FastAPI`
- Consumes: 기존 `daengs_life.app.controllers.walk.router` · `weather.router` · `daengs_life.app.deps.get_cache`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`backend/tests/test_realtime_service_split.py` 를 만든다:

```python
"""realtime 전용 앱과 분리 갈림길의 회귀 가드 (D-070).

**왜 파일 하나인가** — 이 셋은 같은 결정의 세 면이다: 앱이 realtime 만 담는가,
갈림길이 기본값에서 옛 경로로 가는가, 프록시가 503 본문을 안 뭉개는가.
따로 두면 하나를 고칠 때 나머지 둘을 안 보게 된다.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI


def _paths(app: FastAPI) -> set[str]:
    return {r.path for r in app.routes if hasattr(r, "path")}


def test_realtime_app_serves_exactly_the_two_realtime_routes():
    """`/life/ask` 가 들어오면 RAG 스택이 이미지에 필요해진다 — 그것이 이 단언의 요지다."""
    from daengs_life.app.realtime_main import app

    paths = _paths(app)
    assert "/life/walk-conditions" in paths
    assert "/weather/at" in paths
    assert "/life/ask" not in paths


def test_realtime_app_does_not_warm_up_the_encoder():
    """예열을 부르면 `ml` 없는 이미지에서 기동이 느려지거나 로그가 시끄러워진다."""
    import inspect

    from daengs_life.app import realtime_main

    source = inspect.getsource(realtime_main)
    assert "warm_up_encoder" not in source
    assert "get_encoder" not in source
```

- [ ] **Step 2: 실패를 확인한다**

```powershell
cd backend; uv run pytest tests/test_realtime_service_split.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'daengs_life.app.realtime_main'`

- [ ] **Step 3: 앱을 만든다**

`backend/src/daengs_life/app/realtime_main.py`:

```python
"""realtime 전용 ASGI 앱 — Cloud Run 서비스가 띄우는 것 (D-070).

**`app/main.py` 와 왜 갈랐나.** 저쪽은 `/life/ask` 까지 등록한다. 이 이미지에는 `ml`
그룹(torch)이 없어서 그 import 가 깨질 수 있고, 안 깨져도 **realtime 서비스가 RAG 코드를
들고 있는 것**은 분리의 취지에 안 맞는다. 저쪽 파일은 손대지 않았다 — 개발 PC 에서
`uvicorn daengs_life.app.main:app` 으로 셋 다 띄우는 길은 그대로다.

**예열이 없다.** 여기에는 임베딩 모델이 없으므로 올릴 것도 놓을 것도 없다.
`get_cache()` 만 미리 연다 — 첫 요청에 미루면 Redis 커넥션 수립 시간이 그 요청의
8초 예산(⑤-b)에 얹힌다.

인증은 여기 없다. **Cloud Run 의 IAM 이 문이고**, 로그인 검사는 `daengs_backend` 가 한다
(D-070 §5). 그래서 이 앱은 `daengs_backend` 를 여전히 모른다 — D-018 의 방향 그대로다.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI

from daengs_life.app.controllers import walk, weather
from daengs_life.app.deps import get_cache


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    # 연결이 안 돼도 앱은 뜬다 — `Cache()` 가 스스로 메모리로 떨어진다 (RT-001 ④-c).
    # 다만 그 판단이 프로세스 생애에 한 번뿐이라(lru_cache), Redis 가 늦게 뜨는 환경에서는
    # 그 인스턴스가 끝까지 메모리 캐시로 돈다.
    get_cache()
    yield
    get_cache.cache_clear()


def create_app() -> FastAPI:
    app = FastAPI(
        title="DAENGS 실시간 산책·날씨",
        summary="산책 적합도 + 과거 관측. 상태는 Redis 에 있고 이 프로세스에는 없다",
        lifespan=lifespan,
    )
    # CORS 를 열지 않는다. 브라우저가 이 서비스를 직접 부르는 일이 없다 —
    # 부르는 것은 daengs_backend 하나이고 그것은 서버 대 서버다 (D-070 §5 ⓐ).
    app.include_router(walk.router)
    app.include_router(weather.router)

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, str]:
        """Cloud Run 이 컨테이너를 살아 있다고 볼 자리. 외부 API 를 부르지 않는다."""
        return {"status": "ok"}

    return app


app = create_app()

__all__ = ["app", "create_app"]
```

- [ ] **Step 4: 테스트가 통과하는지 본다**

```powershell
cd backend; uv run pytest tests/test_realtime_service_split.py -v
```
Expected: PASS (2건)

- [ ] **Step 5: 의존성 그룹을 넣는다**

```powershell
cd backend
uv add --group realtime "fastapi[standard]>=0.141.1" "httpx>=0.28.1" "pydantic-settings>=2.15.0" "pyyaml>=6.0.3" "redis>=6.0" "tzdata>=2026.3"
```

그다음 `backend/pyproject.toml` 의 그 블록 **위에** 주석을 손으로 단다 (Edit 도구):

```toml
# realtime 서비스(D-070)가 받는 것 전부. **base 의존성을 안 받는다** —
# `uv sync --frozen --only-group realtime --no-install-project` 로 깐다.
#
# base 를 안 받는 이유 둘. ① base 에는 realtime 이 안 쓰는 것이 스무 개쯤 있고
# (sqlalchemy·celery·langgraph·kiwipiepy·shapely·google-genai …) **앞으로 늘어난다.**
# 다른 팀원이 base 에 무거운 것을 넣으면 이 이미지가 따라 커지는데 그 사람은 모른다.
# ② 그룹으로 적으면 **경계가 기계가 읽는 형태로 남는다** — `gait` 그룹과 같은 논리다.
#
# ⚠ `tzdata` 는 코드가 직접 import 하지 않는다. `zoneinfo.ZoneInfo("Asia/Seoul")` 이
#   간접적으로 쓴다. 빼면 import 는 되는데 시간대 조회에서 터진다.
# ⚠ **놓친 패키지는 빌드가 아니라 런타임 import 에서 터진다.** Task 2 의 스모크가 그 그물이다.
# ⚠ 이름을 바꾸면 docker/realtime/Dockerfile 의 `--only-group` 인자도 같이 고쳐라.
```

- [ ] **Step 6: 그룹만으로 앱이 뜨는지 별도 venv 로 확인한다**

```powershell
cd backend
$env:UV_PROJECT_ENVIRONMENT="C:/Users/403/AppData/Local/Temp/rt-venv"
uv sync --frozen --only-group realtime --no-install-project
$env:PYTHONPATH="$PWD/src"
& C:/Users/403/AppData/Local/Temp/rt-venv/Scripts/python.exe -c "import daengs_life.app.realtime_main as m; print(sorted(r.path for r in m.app.routes if hasattr(r,'path')))"
Remove-Item -Recurse -Force C:/Users/403/AppData/Local/Temp/rt-venv
$env:UV_PROJECT_ENVIRONMENT=$null; $env:PYTHONPATH=$null
```
Expected: 경로 목록이 나오고 `ImportError` 가 없다. **여기서 터지는 패키지가 그룹에서 빠진 것이다** — 그러면 `uv add --group realtime <그것>` 을 하고 다시 돌린다.

- [ ] **Step 7: 커밋**

```bash
git add backend/pyproject.toml backend/uv.lock backend/src/daengs_life/app/realtime_main.py backend/tests/test_realtime_service_split.py
git commit -m "feat: realtime 전용 ASGI 앱과 realtime 의존성 그룹 (D-070)"
```

---

## Task 2: 이미지와 로컬 스모크

**Files:**
- Create: `docker/realtime/Dockerfile`, `docker/realtime/Dockerfile.dockerignore`

**Interfaces:**
- Consumes: Task 1 의 `daengs_life.app.realtime_main:app` · `realtime` 그룹
- Produces: 이미지 하나. `PORT` 환경변수로 듣고 `/healthz` 가 200

- [ ] **Step 1: Dockerfile 을 쓴다**

`docker/realtime/Dockerfile`:

```dockerfile
# 실시간 산책·날씨 서비스 (D-070) — Cloud Run.
#
# **파이프라인 이미지와 코드 취급이 반대다.** 저쪽(#427)은 코드를 GCS 에서 받는다 —
# 하루 한 번 도는 잡이라 기동 비용이 싸고, 코드 배포에서 이미지 굽기를 없애는 것이 값이었다.
# 여기는 **이미지에 굽는다**: ① scale-to-zero 는 콜드 스타트가 잦은데 거기에 파일 복사가
# 붙으면 그만큼 느려진다 ② 서비스는 "지금 어느 코드가 도는가"가 **배포 단위와 같아야**
# 롤백이 된다.
#
# **`--no-install-project` + `PYTHONPATH` 도 #427 이 찾은 것과 같은 수다** —
# pyproject.toml 의 `[tool.uv.build-backend] module-name` 이 여덟 패키지를 나열해서
# 일부만 이미지에 넣으면 설치 단계가 깨진다. 설치하지 않고 경로만 알려 주면 그 제약이 없다.
ARG PYTHON_VERSION=3.12.13
ARG UV_VERSION=0.12.3

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uvbin

FROM python:${PYTHON_VERSION}-slim
COPY --from=uvbin /uv /uvx /usr/local/bin/
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONPATH=/app/src

# 의존성. **`--only-group` 이라 base 의존성이 안 들어온다** (pyproject.toml 의 그 블록 주석).
# README.md 도 필요하다 — pyproject.toml 의 readme= 를 uv 가 빌드 메타데이터로 읽는다.
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN uv sync --frozen --only-group realtime --no-install-project

# 코드. `realtime/` 만 넣지 않고 `daengs_life` 를 통째로 넣는 이유 —
# `realtime/config.py` 가 `daengs_life.crawler.core.config` 를 부른다(경로 탐색 · KST ·
# .env 병합 순서, RT-001 ①-2). 그 20줄을 복사하면 두 벌이 되고 한쪽만 늙는다.
COPY backend/src/daengs_life ./src/daengs_life

# `DAENGS_DATA_DIR` 을 주지 않는다. 이미지에 저장소 루트가 없어 `DATA_DIR` 이 스스로
# None 이 되고, realtime 은 파일을 안 읽으니 그게 맞는 값이다 (D-070 §6-5).
#
# 포트는 Cloud Run 이 `PORT` 로 준다. sh 를 거치는 이유가 그 치환이다.
CMD ["sh", "-c", "exec /opt/venv/bin/uvicorn daengs_life.app.realtime_main:app --host 0.0.0.0 --port ${PORT:-8080}"]
```

`docker/realtime/Dockerfile.dockerignore`:

```
# 빌드 컨텍스트는 저장소 루트다. 안 좁히면 frontend/node_modules 와 data/raw 까지
# Cloud Build 로 올라간다.
*
!backend/pyproject.toml
!backend/uv.lock
!backend/README.md
!backend/src/daengs_life
backend/src/daengs_life/**/__pycache__
```

- [ ] **Step 2: 로컬에서 굽는다**

```powershell
cd C:/Users/403/Documents/workspace/DAENGS_dev
$env:DOCKER_BUILDKIT="1"
docker build -f docker/realtime/Dockerfile -t daengs-realtime:local .
```
Expected: 성공. `--only-group` 이 base 를 안 받으므로 파이프라인 이미지(2.3GB)보다 훨씬 작다.

- [ ] **Step 3: 크기를 잰다 (설계 문서의 추정을 실측으로 바꾼다)**

```powershell
docker images daengs-realtime:local --format "{{.Size}}"
```
이 숫자를 적어 둔다 — Task 6 에서 `realtime-service.md` §7 ③ 에 넣는다.

- [ ] **Step 4: 띄우고 두 엔드포인트를 실제로 부른다 — 이것이 그룹의 그물이다**

```powershell
docker run --rm -d --name rt-smoke -p 8099:8080 -e PORT=8080 daengs-realtime:local
docker logs rt-smoke
curl.exe -s -o NUL -w "healthz=%{http_code}`n" http://127.0.0.1:8099/healthz
curl.exe -s -o NUL -w "walk=%{http_code}`n" "http://127.0.0.1:8099/life/walk-conditions?lat=37.4979&lon=127.0276"
curl.exe -s -o NUL -w "openapi=%{http_code}`n" http://127.0.0.1:8099/openapi.json
docker logs rt-smoke | Select-Object -Last 30
docker stop rt-smoke
```
Expected:
- `healthz=200`
- `walk=` **200 또는 503 둘 다 통과다.** API 키가 없으므로 판정이 안 나오는 것이 정상이고, 여기서 보려는 것은 **`ModuleNotFoundError` 없이 코드가 끝까지 도는가**다. 로그에 `ModuleNotFoundError` 나 `ImportError` 가 있으면 그 패키지를 `uv add --group realtime` 하고 Step 2 로 돌아간다.
- `openapi=200` — 응답 모델(`WalkOut`·`WeatherAtOut`)이 스키마로 풀린다는 뜻이라 pydantic 쪽 누락도 같이 잡힌다.

- [ ] **Step 5: 커밋**

```bash
git add docker/realtime/
git commit -m "build: realtime 서비스 이미지 (D-070)"
```

---

## Task 3: backend 설정과 HTTP 클라이언트

**Files:**
- Modify: `backend/src/daengs_backend/config.py`
- Create: `backend/src/daengs_backend/services/realtime_client.py`
- Test: `backend/tests/test_realtime_service_split.py` (추가)

**Interfaces:**
- Produces:
  - `settings.realtime_url: str` (빈 문자열이 기본)
  - `realtime_client.get_walk(lat: float, lon: float) -> tuple[int, Any]` — `(status_code, json)`
  - `realtime_client.post_weather_at(payload: dict) -> tuple[int, Any]`
  - `realtime_client.RealtimeUnavailable(Exception)`

- [ ] **Step 1: 설정 한 줄을 더한다**

`backend/src/daengs_backend/config.py` 의 `crawl_backend` 선언 **바로 위**에 (Edit 도구):

```python
    # 실시간 산책·날씨를 어디서 부르나 (D-070).
    #
    # **비어 있으면 지금까지와 똑같다** — 같은 프로세스의 함수를 부른다. 값이 있으면 그
    # 주소의 Cloud Run 서비스를 HTTP 로 부른다. 개발 PC·개발서버는 비워 두고 GCP VM 의
    # backend/.env 에만 넣는다.
    #
    # **되돌리기가 이 한 줄이다.** 지우고 `docker compose up -d backend` 로 컨테이너를
    # 다시 만들면 분리 전 경로로 돌아온다 (`env_file` 은 컨테이너를 만들 때 굳는다).
    realtime_url: str = ""
```

- [ ] **Step 2: 실패하는 테스트를 쓴다**

`backend/tests/test_realtime_service_split.py` 뒤에 붙인다:

```python
def test_realtime_url_defaults_to_empty_so_nothing_changes_by_default():
    from daengs_backend.config import Settings

    assert Settings().realtime_url == ""


def test_client_keeps_status_and_body_untouched_on_503(monkeypatch):
    """`/life/walk-conditions` 는 판정 불가일 때 503 **본문에 응답 전체**를 싣는다
    (`controllers/walk.py`). 프런트가 그것을 읽으므로 502 로 뭉개면 안 된다."""
    import httpx

    from daengs_backend.services import realtime_client

    body = {"detail": {"now": {"grade": "unknown"}, "sources": [{"provider": "ncst", "ok": False}]}}

    def fake_request(self, request):  # noqa: ANN001
        return httpx.Response(503, json=body, request=request)

    monkeypatch.setattr(httpx.Client, "send", fake_request)
    monkeypatch.setattr(realtime_client, "_id_token", lambda audience: "test-token")

    status, payload = realtime_client.get_walk(37.4979, 127.0276, base_url="https://x.example")
    assert status == 503
    assert payload == body


def test_client_turns_transport_failure_into_its_own_error(monkeypatch):
    import httpx

    from daengs_backend.services import realtime_client

    def boom(self, request):  # noqa: ANN001
        raise httpx.ConnectTimeout("nope", request=request)

    monkeypatch.setattr(httpx.Client, "send", boom)
    monkeypatch.setattr(realtime_client, "_id_token", lambda audience: "test-token")

    with pytest.raises(realtime_client.RealtimeUnavailable):
        realtime_client.get_walk(37.4979, 127.0276, base_url="https://x.example")
```

- [ ] **Step 3: 실패를 확인한다**

```powershell
cd backend; uv run pytest tests/test_realtime_service_split.py -v
```
Expected: FAIL — `No module named 'daengs_backend.services.realtime_client'`

- [ ] **Step 4: 클라이언트를 만든다**

`backend/src/daengs_backend/services/realtime_client.py`:

```python
"""실시간 서비스(Cloud Run)를 부르는 얇은 래퍼 (D-070).

**응답을 해석하지 않는다.** 상태 코드와 JSON 을 그대로 돌려준다 — `/life/walk-conditions`
는 판정 불가일 때 **503 본문에 응답 전체**를 싣고(`daengs_life` 의 `controllers/walk.py`)
프런트가 그것을 읽는다(`ask-inspect.tsx`). 여기서 502 로 뭉개면 콘솔 패널이 조용히 망가진다.

**인증은 Google 서명 ID 토큰이다.** 대상 서비스 URL 이 audience 다. 자격 증명은 VM 의
메타데이터 서버(ADC)에서 오고 키 파일이 없다 — `services/cloudrun_jobs.py` 와 같은 출처이고
클라이언트만 다르다(잡은 `run_v2`, 서비스는 이 토큰).

`daengs_life` 를 import 하지 않는다. 이 파일이 아는 것은 **경로 두 개와 JSON** 뿐이다.
"""
from __future__ import annotations

from typing import Any

import httpx

#: 콜드 부팅(1~2초) + 판정 예산 8초 + 여유. 정적 수집이 도는 콜드 캐시는 이보다 오래
#: 걸릴 수 있는데, 그때는 부르는 쪽이 저하로 닫는다 (finalize 는 `status="failed"`).
TIMEOUT_SEC = 15.0


class RealtimeUnavailable(Exception):
    """전송이 실패했다 — 응답 자체를 못 받았다. HTTP 오류 응답은 여기 안 온다."""


def _id_token(audience: str) -> str:
    """ADC 로 대상 서비스용 ID 토큰을 받는다. 테스트가 이 이름을 갈아끼운다."""
    import google.auth.transport.requests
    import google.oauth2.id_token

    return google.oauth2.id_token.fetch_id_token(
        google.auth.transport.requests.Request(), audience
    )


def _call(method: str, path: str, *, base_url: str, **kwargs: Any) -> tuple[int, Any]:
    root = base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {_id_token(root)}"}
    try:
        with httpx.Client(timeout=TIMEOUT_SEC) as client:
            response = client.request(method, f"{root}{path}", headers=headers, **kwargs)
    except httpx.HTTPError as exc:
        raise RealtimeUnavailable(f"{type(exc).__name__}: {exc}") from exc
    try:
        return response.status_code, response.json()
    except ValueError:
        # JSON 이 아닌 응답(예: IAM 이 막은 403 의 HTML). 본문을 문자열로 실어 보낸다 —
        # 뭉개면 "왜 막혔는지"가 로그에서 사라진다.
        return response.status_code, {"detail": response.text[:512]}


def get_walk(lat: float, lon: float, *, base_url: str) -> tuple[int, Any]:
    return _call("GET", "/life/walk-conditions", base_url=base_url,
                 params={"lat": lat, "lon": lon})


def post_weather_at(payload: dict[str, Any], *, base_url: str) -> tuple[int, Any]:
    return _call("POST", "/weather/at", base_url=base_url, json=payload)


__all__ = ["RealtimeUnavailable", "TIMEOUT_SEC", "get_walk", "post_weather_at"]
```

- [ ] **Step 5: 테스트가 통과하는지 본다**

```powershell
cd backend; uv run pytest tests/test_realtime_service_split.py -v
```
Expected: PASS (5건)

- [ ] **Step 6: 커밋**

```bash
git add backend/src/daengs_backend/config.py backend/src/daengs_backend/services/realtime_client.py backend/tests/test_realtime_service_split.py
git commit -m "feat: 실시간 서비스 HTTP 클라이언트와 realtime_url 설정 (D-070)"
```

---

## Task 4: 세 접점을 갈림길로 배선

**Files:**
- Create: `backend/src/daengs_backend/routers/life_walk.py`
- Modify: `backend/src/daengs_backend/main.py`, `backend/src/daengs_backend/orchestration/adapters/life.py`
- Test: `backend/tests/test_realtime_service_split.py` (추가)

**Interfaces:**
- Consumes: Task 3 의 `realtime_client.get_walk` · `post_weather_at` · `RealtimeUnavailable` · `settings.realtime_url`
- Produces: `routers.life_walk.router` (`/life/walk-conditions`, 인증 없음 — 등록 시점에 건다)

⚠ **기본값(`realtime_url == ""`)에서는 옛 경로가 글자 그대로 그대로여야 한다.** 그래야 기존 테스트(`test_ask_auth.py` · `tests/walk/api/test_walk_auth.py` 등)가 하나도 안 바뀐다.

- [ ] **Step 1: 프록시 라우터를 만든다**

`backend/src/daengs_backend/routers/life_walk.py`:

```python
"""`/life/walk-conditions` 프록시 (D-070).

**`DAENGS_REALTIME_URL` 이 있을 때만 등록된다.** 비어 있으면 `main.py` 가 예전처럼
`daengs_life` 의 라우터를 그대로 등록하므로, 이 파일은 그 환경에서 아예 안 쓰인다.

**상태 코드와 본문을 손대지 않는다.** 200 도 503 도 422 도 그대로 넘긴다 —
503 본문에는 어느 출처가 죽었는지가 들어 있고 프런트가 그것을 읽는다.

인증은 여기 없다. `main.py` 가 등록 시점에 건다 — `daengs_life` 쪽 라우터와 **같은 자리,
같은 의존성**이라 앱 회원·관리자 판정이 안 갈린다.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response, status

from daengs_backend.config import settings
from daengs_backend.services import realtime_client

router = APIRouter(tags=["Life · 산책 적합도"])


@router.get("/life/walk-conditions", summary="산책 적합도 (실시간 서비스로 전달)")
def get_walk(
    response: Response,
    lat: float = Query(..., ge=33.0, le=39.0, description="위도 (WGS84)"),
    lon: float = Query(..., ge=124.0, le=132.0, description="경도 (WGS84)"),
) -> Any:
    try:
        code, payload = realtime_client.get_walk(lat, lon, base_url=settings.realtime_url)
    except realtime_client.RealtimeUnavailable as exc:
        # 전송 실패는 **우리 쪽 저하**다. 실시간 서비스가 낸 503(판정 불가)과 갈라 둔다 —
        # 둘을 같은 코드로 주면 "출처가 죽었나 서비스가 죽었나"를 화면에서 못 가른다.
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            {"code": "realtime_unavailable", "message": f"실시간 서비스에 닿지 못했습니다: {exc}"},
        ) from exc
    response.status_code = code
    return payload


__all__ = ["router"]
```

- [ ] **Step 2: `main.py` 를 갈림길로 바꾼다**

`backend/src/daengs_backend/main.py` 의 `app.include_router(walk.router, dependencies=...)` 한 줄을 (Edit 도구로) 이렇게 바꾼다. **위의 주석 블록은 그대로 두고 아래에 덧붙인다:**

```python
# 🔴 D-070 — 분리 갈림길. `DAENGS_REALTIME_URL` 이 있으면 이 앱은 **판정을 하지 않고
# 전달만** 합니다. 인증은 두 갈래에서 **같은 의존성**이라 앱 회원·관리자 판정이 안 갈립니다.
#
# 비어 있을 때의 줄은 분리 전과 **글자 그대로 같습니다** — 그래야 되돌리기가 변수 하나가 되고,
# 개발 PC·개발서버의 기존 동작과 테스트가 안 바뀝니다.
if settings.realtime_url:
    app.include_router(life_walk.router, dependencies=[Depends(admin_or_app_user(Perm.READ))])
else:
    app.include_router(walk.router, dependencies=[Depends(admin_or_app_user(Perm.READ))])
```

그리고 import 를 더한다 (`from daengs_backend.routers import walk as app_walks` 근처):

```python
from daengs_backend.routers import life_walk
```

⚠ `from daengs_life.app.controllers import ask, walk` 는 **지우지 않는다.** 갈림길의 한쪽이 여전히 쓴다.

- [ ] **Step 3: 어댑터 둘을 갈림길로 바꾼다**

`backend/src/daengs_backend/orchestration/adapters/life.py` 의 `_walk_life` 를 이렇게 바꾼다:

🔴 **계획을 고쳤다 (Ruling 2, 2026-09-11 사전 점검).** 처음 안은 HTTP 503 을 `HTTPException`
으로 올리게 돼 있었는데 **그러면 분리 전과 동작이 달라진다:**

| | 분리 전 | 처음 안대로 하면 |
| --- | --- | --- |
| 판정 불가일 때 | `walk()` 서비스는 **예외를 안 낸다.** `grade="unknown"` 인 `WalkOut` 을 준다. 503 을 만드는 것은 그 위의 **HTTP 컨트롤러**이고 어시스턴트는 거기를 안 지난다 → 어댑터가 그것을 보고 **`CapabilityStatus.ABSTAINED`**(`code="unknown_verdict"`) 로 닫는다 | HTTP 503 → `HTTPException` → `WalkCapabilityAdapter` 의 `except Exception` → **`CapabilityStatus.ERROR`** |

🔴 **위 표의 `ABSTAINED` 는 2026-09-11 에 정정한 것이다.** 처음에는 `OK` 라고 적었는데 틀렸다 —
`orchestration/adapters/walk.py:47~57` 이 `upstream.now.grade == "unknown"` 을 보고
**`ABSTAINED`** 로 닫는다. `OK` 는 등급이 GOOD·CAUTION·UNSAFE 일 때뿐이다. **결론(예외를 올리지
않는다)은 그대로다** — 바뀐 것은 "무엇이 보존되는가"의 이름이고, 보존되는 것은 `OK` 가 아니라
`ABSTAINED` 다.

같은 날씨 상황에서 어시스턴트 답이 달라진다 — 기준 ①(안 깨뜨린다)의 정면 위반이다.
**503 의 본문이 곧 `WalkOut` 전체**이므로(`controllers/walk.py:37` 이
`detail=result.model_dump(mode="json", by_alias=True)` 로 싣는다) 되돌려서 정상 반환한다.

```python
def _walk_life(payload: WalkPayload) -> Any:
    """산책 적합도. `DAENGS_REALTIME_URL` 이 있으면 HTTP, 없으면 같은 프로세스 (D-070).

    **503 을 예외로 올리지 않는다.** 분리 전에 이 자리에 오던 것은 `walk()` 서비스의 반환값
    이고, 그것은 판정 불가에도 예외를 내지 않았다 — 503 을 만드는 것은 그 위의 HTTP
    컨트롤러이고 어시스턴트는 거기를 안 지난다. 여기서 올리면 같은 날씨에 어시스턴트 답이
    `OK`(모른다)에서 `ERROR`(실행 실패)로 바뀐다.

    다행히 그 503 의 본문이 **`WalkOut` 전체**다 (`controllers/walk.py` 가
    `detail=result.model_dump(mode="json", by_alias=True)` 로 싣는다). 그래서 되돌릴 수 있다.
    """
    from daengs_backend.config import settings

    if settings.realtime_url:
        from daengs_life.app.dto.walk import WalkOut

        from daengs_backend.services import realtime_client

        code, body = realtime_client.get_walk(
            payload.lat, payload.lon, base_url=settings.realtime_url
        )
        if code == 503 and isinstance(body, dict) and isinstance(body.get("detail"), dict):
            # 판정 불가 — 분리 전과 같이 **정상 반환**이다.
            return WalkOut.model_validate(body["detail"])
        if code >= 400:
            # 4xx 와 그 밖의 5xx 는 분리 전에 없던 상황이다(HTTP 경계가 없었으니까).
            # 어댑터가 ERROR 로 닫게 예외로 올린다.
            raise RuntimeError(f"실시간 서비스 {code}: {body}")
        return WalkOut.model_validate(body)

    from daengs_life.app.deps import get_cache, get_now
    from daengs_life.app.services.walk import walk
    from daengs_life.realtime.geo import LatLon

    return walk(LatLon(payload.lat, payload.lon), get_now(), cache=get_cache())
```

⚠ **`WalkOut.model_validate(body["detail"])` 가 alias 직렬화를 되돌리는지 테스트로 확인하라.**
`by_alias=True` 로 나간 값이라 `WindowOut` 의 `from` 같은 별칭이 들어 있다. Task 4 Step 4 에
그 단언을 더한다.

그리고 `_weather_at_life` 의 머리를 이렇게 바꾼다 — **`result` 를 만드는 방식만 갈리고 아래의 원자 추출은 그대로다:**

```python
def _weather_at_life(lat: float, lon: float, observed_at: datetime) -> WalkWeatherObservation:
    """Life의 공개 DTO 경계에서 과거 관측을 읽고 Walk용 값만 남긴다.

    `DAENGS_REALTIME_URL` 이 있으면 그 경계를 HTTP 로 넘는다 (D-070). **아래의 원자 추출은
    한 벌 그대로다** — 두 갈래가 같은 `WeatherAtOut` 을 보기 때문이고, 그래야 분리 때문에
    산책 기록에 박히는 값이 달라지는 일이 없다.
    """
    from daengs_backend.config import settings
    from daengs_life.app.dto.weather import WeatherAtOut, WeatherAtRequest

    if settings.realtime_url:
        from daengs_backend.services import realtime_client

        code, body = realtime_client.post_weather_at(
            {"lat": lat, "lon": lon, "observed_at": observed_at.isoformat()},
            base_url=settings.realtime_url,
        )
        if code >= 400:
            raise RuntimeError(f"실시간 서비스 {code}: {body}")
        result = WeatherAtOut.model_validate(body)
    else:
        from daengs_life.app.deps import get_cache, get_now
        from daengs_life.app.services.weather import weather_at

        result = weather_at(
            WeatherAtRequest(lat=lat, lon=lon, observed_at=observed_at),
            get_now(),
            cache=get_cache(),
        )
    atoms = {item.quantity: item for item in result.observations}
    # …(아래는 기존 코드 그대로)
```

⚠ **`RuntimeError` 로 던지는 것이 맞다** — 이 함수를 감싸는 `lookup_walk_weather` 가 모든 예외를 잡아 `status="failed"` 로 닫는다(`adapters/life.py:153`). 그래서 산책 finalize 는 실패하지 않는다.

- [ ] **Step 4: 갈림길 테스트를 더한다**

`backend/tests/test_realtime_service_split.py` 뒤에:

```python
def test_default_registration_still_uses_the_in_process_router():
    """기본값에서 라우트가 `daengs_life` 것이어야 한다 — 아니면 기존 인증 테스트가 거짓 통과한다."""
    from daengs_backend.config import settings
    from daengs_backend.main import app

    assert settings.realtime_url == ""
    endpoints = {
        r.path: getattr(r, "endpoint", None) for r in app.routes if hasattr(r, "path")
    }
    walk_endpoint = endpoints["/life/walk-conditions"]
    assert walk_endpoint.__module__ == "daengs_life.app.controllers.walk"


def test_weather_lookup_reduces_http_body_the_same_way(monkeypatch):
    """HTTP 갈래와 in-process 갈래가 **같은 `WeatherAtOut`** 을 보므로 원자 추출이 한 벌이다."""
    from datetime import datetime, timezone

    from daengs_backend.orchestration.adapters import life as life_adapter

    body = {
        "status": "captured",
        "requested_at": "2026-09-11T05:00:00+09:00",
        "fetched_at": "2026-09-11T05:10:00+09:00",
        "grid": [61, 125],
        "observations": [
            {"quantity": "temp_c", "representation": "number", "value": 21.5,
             "source": "ncst", "spatial_ref": "격자 61,125",
             "valid_at": "2026-09-11T05:00:00+09:00", "issued_at": "2026-09-11T05:00:00+09:00"},
        ],
        "sources": [{"provider": "ncst", "outcome": "ok", "calls": 1}],
    }
    monkeypatch.setattr(life_adapter.settings if hasattr(life_adapter, "settings") else object(), "x", None, raising=False)

    from daengs_backend.config import settings as backend_settings

    monkeypatch.setattr(backend_settings, "realtime_url", "https://rt.example")
    monkeypatch.setattr(
        "daengs_backend.services.realtime_client.post_weather_at",
        lambda payload, *, base_url: (200, body),
    )

    observed = datetime(2026, 9, 11, 5, 0, tzinfo=timezone.utc)
    out = life_adapter._weather_at_life(37.4979, 127.0276, observed)
    assert out.status == "captured"
    assert out.temperature_c == 21.5
```

- [ ] **Step 5: 영향 받는 기존 테스트까지 돌린다**

```powershell
cd backend
uv run pytest tests/test_realtime_service_split.py tests/test_ask_auth.py tests/test_ask_api.py tests/test_main_stays_light.py tests/test_orchestration_adapters.py tests/walk -v
```
Expected: 전부 PASS. **하나라도 깨지면 갈림길의 기본값이 옛 경로와 같지 않다는 뜻이다** — 고칠 곳은 테스트가 아니라 `main.py` 다.

- [ ] **Step 6: 전체 테스트**

```powershell
cd backend; uv run pytest
```
Expected: PASS (약 9분, skip 15건은 정상 — 버리는 DB 가 필요한 것들)

- [ ] **Step 7: 커밋**

```bash
git add backend/src/daengs_backend/ backend/tests/test_realtime_service_split.py
git commit -m "feat: 실시간 접점 셋을 DAENGS_REALTIME_URL 갈림길로 (D-070)"
```

---

## Task 5: 배포 스크립트와 GCP 배포

**Files:**
- Create: `infra/gcp/realtime.sh`, `infra/gcp/realtime-teardown.sh`
- Modify: `infra/gcp/README.md`

**Interfaces:**
- Consumes: Task 2 의 이미지 · Task 1 의 그룹
- Produces: Cloud Run 서비스 `daengs-realtime` 과 그 URL

⚠ **방화벽 규칙은 만들지 않는다.** 2026-09-11 실측에서 `default-allow-internal` 이 이미 덮는 것이 확인됐다.

- [ ] **Step 1: 배포 스크립트를 쓴다**

`infra/gcp/realtime.sh` — `pipeline.sh` 의 모양을 따른다. 담을 것:

```bash
#!/usr/bin/env bash
# 실시간 산책·날씨 서비스 배포 (D-070).
#   PROJECT=daengs bash infra/gcp/realtime.sh
#
# ⚠ Git Bash 에서 돌린다. `MSYS_NO_PATHCONV=1` 을 켜지 마라 — gcloud 자체가 깨진다
#   (2026-09-11 실측: can't open file 'C:\c\Program Files...gcloud.py').
set -euo pipefail

PROJECT="${PROJECT:-daengs}"
REGION=asia-northeast3
SERVICE=daengs-realtime
SA_EMAIL="corpus-pipeline@${PROJECT}.iam.gserviceaccount.com"
IMAGE_BASE="${REGION}-docker.pkg.dev/${PROJECT}/daengs/realtime"
VM_INTERNAL_IP="${VM_INTERNAL_IP:-10.178.0.2}"

# 태그는 커밋이 아니라 **이미지 입력의 내용 해시**다 (#427 과 같은 규칙).
# 여기 목록에 없는 파일을 고쳐도 이미지는 안 바뀐다 — 그래서 목록이 곧 계약이다.
SHA="$(git ls-files -s backend/pyproject.toml backend/uv.lock backend/README.md \
        backend/src/daengs_life docker/realtime | git hash-object --stdin | cut -c1-7)"

echo "== 시크릿 (없으면 만들고, 값은 사람이 넣는다)"
for s in realtime-redis-url realtime-kakao-key realtime-kma-hub-key; do
  gcloud secrets describe "$s" >/dev/null 2>&1 || {
    gcloud secrets create "$s" --replication-policy=automatic
    echo "  ⚠ ${s} 가 비어 있다. 값을 넣어라:"
    echo "     printf %s '<값>' | gcloud secrets versions add ${s} --data-file=-"
  }
  gcloud secrets add-iam-policy-binding "$s" \
    --member="serviceAccount:${SA_EMAIL}" --role=roles/secretmanager.secretAccessor >/dev/null
done
gcloud secrets add-iam-policy-binding corpus-data-go-kr-key \
  --member="serviceAccount:${SA_EMAIL}" --role=roles/secretmanager.secretAccessor >/dev/null

echo "== 이미지 (Cloud Build)"
if gcloud artifacts docker images describe "${IMAGE_BASE}:${SHA}" >/dev/null 2>&1; then
  echo "(이미지 ${IMAGE_BASE}:${SHA} 이미 있음 — 빌드 생략)"
else
  cfg="$(mktemp)"
  cat > "$cfg" <<CFG
steps:
  - name: gcr.io/cloud-builders/docker
    env: ['DOCKER_BUILDKIT=1']
    args: ['build', '-f', 'docker/realtime/Dockerfile', '-t', '${IMAGE_BASE}:${SHA}', '.']
images: ['${IMAGE_BASE}:${SHA}']
CFG
  gcloud builds submit --config="$cfg" .
  rm -f "$cfg"
fi

echo "== 서비스 배포"
# --min-instances=0 이 이 카드의 요점이다. 인터넷에는 안 연다 (인증은 backend 가 한다).
gcloud run deploy "${SERVICE}" --region="${REGION}" --image="${IMAGE_BASE}:${SHA}" \
  --service-account="${SA_EMAIL}" \
  --no-allow-unauthenticated \
  --network=default --subnet=default --vpc-egress=private-ranges-only \
  --min-instances=0 --max-instances=5 \
  --cpu=1 --memory=512Mi --concurrency=40 --timeout=60s \
  --set-secrets="REDIS_URL=realtime-redis-url:latest,DATA_GO_KR_KEY=corpus-data-go-kr-key:latest,KAKAO_REST_KEY=realtime-kakao-key:latest,KMA_HUB_KEY=realtime-kma-hub-key:latest"

echo "== VM 의 backend 계정에 호출 권한"
# VM 은 기본 컴퓨트 SA 로 메타데이터 인증을 쓴다 (#326 과 같은 방식).
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT}" --format='value(projectNumber)')"
gcloud run services add-iam-policy-binding "${SERVICE}" --region="${REGION}" \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role=roles/run.invoker >/dev/null

URL="$(gcloud run services describe "${SERVICE}" --region="${REGION}" --format='value(status.url)')"
echo
echo "완료. VM 의 backend/.env 에 이 줄을 넣고 컨테이너를 다시 만들어라:"
echo "  DAENGS_REALTIME_URL=${URL}"
```

`infra/gcp/realtime-teardown.sh` — 서비스와 시크릿 셋을 지운다 (`pipeline-teardown.sh` 와 같은 모양).

- [ ] **Step 2: 시크릿 값을 넣는다**

`realtime-redis-url` 은 GCP VM 의 최상단 `.env` 에서 `REDIS_PASSWORD` 를 읽어 만든다:

```bash
ssh -i ~/.ssh/google_compute_engine daengs@34.64.233.102 \
  "grep '^REDIS_PASSWORD=' /srv/daengs/.env | cut -d= -f2-"
```
그 값으로:
```bash
printf %s 'redis://:<암호>@10.178.0.2:6379/0' | gcloud secrets versions add realtime-redis-url --data-file=-
```
`realtime-kakao-key` · `realtime-kma-hub-key` 도 같은 `.env`(또는 `backend/.env`)의 `KAKAO_REST_KEY` · `KMA_HUB_KEY` 로 채운다.

⚠ **암호를 명령줄에 그대로 치면 셸 히스토리에 남는다.** 위처럼 `printf | --data-file=-` 로 넣는다.

- [ ] **Step 3: 배포한다**

```bash
PROJECT=daengs bash infra/gcp/realtime.sh
```

- [ ] **Step 4: 서비스가 Redis 를 실제로 쓰는지 확인한다**

```powershell
$T = gcloud auth print-identity-token
$U = gcloud run services describe daengs-realtime --region=asia-northeast3 --format="value(status.url)"
curl.exe -s -H "Authorization: Bearer $T" -o NUL -w "healthz=%{http_code}`n" "$U/healthz"
curl.exe -s -H "Authorization: Bearer $T" -w "`nHTTP=%{http_code}`n" "$U/life/walk-conditions?lat=37.4979&lon=127.0276"
```
Expected: `healthz=200`. walk 은 200(판정 성공) 또는 503(출처 저하) — **본문의 `sources` 를 읽어 무엇이 죽었는지 본다.**

그리고 **캐시가 정말 공유되는지**를 VM 에서 확인한다 — 이게 안 A 의 핵심 단언이다:

```bash
ssh -i ~/.ssh/google_compute_engine daengs@34.64.233.102 \
  "cd /srv/daengs && docker compose exec -T redis sh -c 'redis-cli -a \$REDIS_PASSWORD --no-auth-warning keys \"rt:*\" | head -20'"
```
Expected: `rt:kma-vilage-fcst:ncst:...` 같은 키가 보인다. **안 보이면 서비스가 메모리 캐시로 떨어진 것이다** — Cloud Run 로그에서 Redis 연결 실패를 찾는다.

- [ ] **Step 5: 인증 없이는 못 부르는지 확인한다**

```powershell
curl.exe -s -o NUL -w "no-auth=%{http_code}`n" "$U/healthz"
```
Expected: `no-auth=403`

- [ ] **Step 6: VM 의 backend 를 갈림길 반대편으로 넘긴다**

```bash
ssh -i ~/.ssh/google_compute_engine daengs@34.64.233.102
# backend/.env 에 DAENGS_REALTIME_URL=<위 URL> 한 줄 추가
# ⚠ env_file 은 컨테이너를 만들 때 굳는다. restart 로는 반영 안 된다.
# ⚠ 셸에 GEMINI_API_KEY 를 올리고 up -d 해야 한다 (CLAUDE.md).
export GEMINI_API_KEY=...
docker compose -f docker-compose.yml -f docker-compose.gcp.yml up -d backend
```

그리고 실제 경로로 확인:
```bash
curl -s -o /dev/null -w "%{http_code}\n" https://daengapi.weareithero.cloud/life/walk-conditions?lat=37.4979&lon=127.0276
```
Expected: **401**(로그인 안 함) — 게이트웨이가 여전히 인증을 걸고 있다는 뜻이다.

- [ ] **Step 7: 콘솔에서 503 본문이 살아 있는지 본다 (검증 ④)**

관리자 콘솔의 「산책 적합도」 점검 패널에서 좌표를 넣어 부른다. 판정이 나오면 200 이고,
출처가 죽어 503 이 오면 **패널에 `sources` 목록이 그대로 보여야** 한다. 안 보이면 프록시가
본문을 뭉갠 것이다.

- [ ] **Step 8: 되돌리기가 되는지 본다 (검증 ⑥)**

VM 의 `backend/.env` 에서 그 줄을 지우고 `up -d backend` → 같은 호출이 여전히 200/503 이면
in-process 경로로 정확히 돌아온 것이다. 확인 후 다시 넣는다.

- [ ] **Step 9: 커밋**

```bash
git add infra/gcp/
git commit -m "build: 실시간 서비스 배포·삭제 스크립트 (D-070)"
```

---

## Task 6: 문서에 실측을 반영한다

**Files:**
- Modify: `docs/deploy/realtime-service.md` · `docs/deploy/runbook.md` · `docs/deploy/roadmap.md` §8 · `docs/decisions.md` D-070 · `CLAUDE.md` · `docs/superpowers/specs/2026-09-11-realtime-cloudrun-design.md`

- [ ] **Step 1: 설계 문서의 🔴/🟡 를 실측으로 바꾼다**

`docs/deploy/realtime-service.md` §7 검증 표의 일곱 줄을 결과로 채운다. 특히:
- ①② → ✅ + 근거 한 줄 (`NOAUTH` 응답 · 서비스 배포 성공)
- ③ → **실제 이미지 크기와 콜드 스타트 초** (Task 2 Step 3 · Task 5 Step 4)
- §4 의 `allow-redis-from-run` 규칙 문단을 **"만들지 않는다 — `default-allow-internal` 이 이미 덮는다(실측)"** 로 고친다

- [ ] **Step 2: Git Bash·PowerShell 함정을 적는다**

`docs/deploy/realtime-service.md` 에 절을 하나 더한다 — **「gcloud 를 Windows 에서 부를 때」**:
`/opt/...` 가 `C:/Program Files/Git/opt/...` 로 바뀌는 것 · `MSYS_NO_PATHCONV=1` 이 gcloud 를
깨뜨리는 것 · PowerShell 이 native 명령의 따옴표를 먹는 것(값은 인자로 넘긴다). 셋 다 2026-09-11 실측.

- [ ] **Step 3: runbook 에 절차를 더한다**

`docs/deploy/runbook.md` 에 「실시간 서비스 (GCP)」 절: 배포 명령 · 시크릿 넣는 법 ·
`DAENGS_REALTIME_URL` 을 VM `.env` 에 넣고 `up -d backend` 하는 것(`restart` 로는 안 된다) ·
되돌리기.

- [ ] **Step 4: 종료 체크리스트에 넣는다**

`docs/deploy/roadmap.md` §8 에 `bash infra/gcp/realtime-teardown.sh` 한 줄.

- [ ] **Step 5: CLAUDE.md 의 「접점 세 줄」 문장을 고친다**

지금 문장은 *"접점은 `main.py` 의 세 줄뿐"* 인데 실제로는 넷이고(오케스트레이션이 셋을 더했다),
이 카드로 **둘이 HTTP 로 갈렸다.** 실제 상태로 고쳐 적는다.

- [ ] **Step 6: 게이트와 커밋**

```powershell
cd backend; uv run check
```

```bash
git add docs/ CLAUDE.md
git commit -m "docs: 실시간 서비스 분리 실측 반영과 운영 절차 (D-070)"
git push
```

- [ ] **Step 7: PR #435 본문을 갱신한다**

`## 무엇을 / 왜` 를 「설계까지」에서 「설계 + 구현」으로, `## 작업 목록` 에 구현 항목을,
`## 배포 영향` 을 **「compose / 환경 변수 변경」 체크**로 바꾼다 (VM `.env` 에 한 줄이 는다).
`## 남은 것` 에서 "구현 전체가 남는다"를 지우고 실제로 남은 것만 남긴다.

---

## Self-Review

**Spec coverage** — `realtime-service.md` §3~§7 의 항목을 하나씩 대조했다: 경계(Task 4) · 경로 보존(Task 4 Step 1) · 503 보존(Task 3 테스트 + Task 5 Step 7) · 의존성 그룹(Task 1) · 이미지(Task 2) · 배포 플래그(Task 5) · 비밀값(Task 5 Step 2) · 검증 일곱(Task 2 Step 4 = ⑦, Task 5 = ③④⑤⑥, ①② 는 이미 섰다) · 안 하는 것(계획 어디에도 8초/30초 변경 · 프리페치 · Memorystore · 개발서버 변경이 없다). **빠진 것 없음.**

**Placeholder scan** — "적절히" · "TBD" · "필요하면" 류 없음. 모든 코드 단계에 실제 코드가 있다. Task 5 Step 1 의 스크립트는 전문이고, Task 6 만 문서 편집이라 서술이다(그건 코드가 아니므로 맞다).

**Type consistency** — `realtime_client.get_walk(lat, lon, *, base_url)` 이 Task 3 정의와 Task 4 사용에서 같다. `post_weather_at(payload, *, base_url)` 도 같다. `RealtimeUnavailable` 이름이 셋에서 같다. `WeatherAtOut` 을 두 갈래가 공유하는 것이 Task 4 Step 3 과 Step 4 테스트에서 일치한다.

**한 가지 남은 위험** — Task 4 Step 4 의 두 번째 테스트에 `monkeypatch.setattr(... raising=False)` 한 줄이 군더더기다. 실행자는 그 줄을 지우고 `backend_settings` 패치만 남겨도 된다.
