# 도감 카드 생성 GPU 서비스 — Qwen-Image-Edit-2511 · FLUX.2-klein-4B 비교 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 오픈 가중치 편집 모델 둘(Qwen-Image-Edit-2511 · FLUX.2-klein-4B)을 diffusers 로 Cloud Run GPU 서비스에 올리고, 지금의 Nano Banana 2(D-074)와 같은 사진·틀·seed 로 비교해 품질(닮음·틀 밀림·제목판)과 서빙 가능성(콜드 스타트·과금 시간)을 잰다.

**Architecture:** 새 패키지 `backend/src/daengs_cardgen/` 가 GPU 서비스(FastAPI, `POST /generate`)이고 전용 그룹 `cardgen` 만 설치한 CUDA 이미지로 Cloud Run(asia-southeast1 L4, min 0 · max 1)에 모델마다 서비스 하나씩 뜬다. backend 쪽은 `daengs_cardimage.engine.HttpCardImageEngine` 이 그 서비스를 부르고, `services/ai_card_engine.py` 가 `DAENGS_CARDGEN_URL` 이 비어 있으면 지금의 Gemini 엔진을 그대로 고른다. 비교는 `backend/tools/cardgen_compare.py` 가 개발 PC 에서 `gcloud run services proxy` 로 연 로컬 포트를 불러 수행한다.

**Tech Stack:** diffusers(`QwenImageEditPlusPipeline` · `Flux2KleinPipeline`) · torch 2.13 CUDA(cu126) · bitsandbytes nf4 · FastAPI · httpx · Pillow · Cloud Run GPU(L4) · Cloud Storage 볼륨 · pytest

**Spec:** 이 계획의 설계 근거는 PR #544 본문 「컨텍스트 메모」와 `docs/cardimage/research-2026-09-15.md` 다(대화에서 사용자가 정한 것 — 별도 spec 문서 없음). 반드시 둘을 같이 읽는다. 옛 결정은 D-074(엔진) · D-076(`daengs_cardimage` 경계) · D-070(Cloud Run 서비스 갈림길) · D-062(GPU 잡·CUDA torch 덮어쓰기).

## Global Constraints

- 후보는 **`qwen-edit-2511`(Qwen/Qwen-Image-Edit-2511) · `klein-4b`(black-forest-labs/FLUX.2-klein-4B)** 둘. 다른 모델을 넣지 않는다.
- `daengs_cardgen` 은 `daengs_backend`·`daengs_cardimage`·`daengs_life`·`sqlalchemy` 를 import 하지 않는다. `torch`·`diffusers`·`transformers`·`bitsandbytes` 는 **함수 안에서만** import 한다.
- `daengs_backend`·`daengs_cardimage` 는 `daengs_cardgen` 을 import 하지 않는다 (HTTP 로만 닿는다).
- `daengs_cardimage` 의 기존 금지 목록(`daengs_backend`·`fastapi`·`sqlalchemy`·`starlette`·`pydantic_settings`)은 그대로다. `httpx` 는 허용.
- `DAENGS_CARDGEN_URL` 이 비어 있으면 backend 동작은 **지금과 글자 그대로 같다**(Gemini 엔진·Gemini 키 확인). 이 카드에서 VM 의 `backend/.env` 에 이 값을 **넣지 않는다** — 앱 경로의 정리 기준(`4 × cardimage_timeout_ms + 60초`)이 콜드 스타트를 모르기 때문이다(「남은 것」).
- 서비스 계약: `POST /generate` JSON `{"images_b64": [틀 PNG, 사진 JPEG], "prompt", "seed", "width", "height", "steps"?, "guidance"?}` → `200 image/png` + 헤더 `X-Cardgen-Model` · `X-Cardgen-Seconds` · `X-Cardgen-Size`. 이미지 순서는 **틀이 먼저**(`build_prompt` 의 "Image 1 is a collectible trading card").
- 헬스 경로는 **`/health`** 다. `/healthz` 는 Cloud Run 앞 구글 프런트엔드가 가로챈다(D-070 실측).
- 생성 크기는 `GEN_SIZE = (1024, 1632)`(16의 배수), 받은 뒤 `CARD_SIZE = (994, 1582)` 로 줄인다.
- 기본 추론값: klein `steps=4, guidance=1.0` · Qwen `steps=40, true_cfg_scale=4.0, negative_prompt=" "`, Qwen 양자화 기본 `CARDGEN_QWEN_QUANT=nf4`(transformer·text_encoder bitsandbytes 4bit).
- Cloud Run: 리전 `asia-southeast1`, `--gpu=1 --gpu-type=nvidia-l4 --no-gpu-zonal-redundancy --cpu=8 --memory=32Gi --concurrency=1 --min-instances=0 --max-instances=1 --timeout=900 --no-allow-unauthenticated --no-cpu-throttling`. 서비스 이름 `daengs-cardgen-klein` · `daengs-cardgen-qwen`. 가중치 버킷 `gs://daengs-cardgen-weights`(asia-southeast1) 를 `/models` 로 마운트, `HF_HOME=/models`. 시작 프로브는 기본 TCP — 포트는 곧바로 열리고 모델은 백그라운드로 올라간다(프로브 상한 240초 안에 Qwen 이 못 올라온다).
- lock 의 torch 는 리눅스 CPU 인덱스 고정을 **풀지 않는다**. CUDA 판은 Dockerfile 에서 `torch==X+cu126`·`torchvision==Y+cu126` 로 덮어쓰고 빌드 때 `torch.version.cuda` 로 검증한다. `uv sync --frozen` 은 Dockerfile 에 한 번만.
- 백엔드 의존성은 `uv add --group cardgen …` 으로만. `pyproject.toml` 을 손으로 고치지 않는다(예외: `[tool.uv.build-backend] module-name` 목록 한 줄).
- **돈이 나가는 명령(Cloud Build · 가중치 받기 · 서비스 배포 · 모델 호출 · Gemini 호출)은 실행 전에 사람에게 무엇을·몇 번·예상 비용을 설명하고 승인받는다.** 하위 에이전트는 이런 명령을 실행하지 않는다 — 해당 Task 는 컨트롤러가 사람과 함께 한다.
- 합성 방식(그림만 잘라 붙이기·배지 합성)을 만들거나 제안하지 않는다. 글자는 지금처럼 Pillow(`title.draw_title`)가 얹는다.
- 파일 읽기·쓰기는 전용 도구로(heredoc·sed 로 파일을 만들지 않는다). 검색은 Grep 도구로.
- gcloud 를 Windows 에서 부를 때 `infra/gcp/README.md` 「자주 걸리는 것」을 따른다 — Git Bash 는 `MSYS2_ARG_CONV_EXCL="--add-volume-mount"`, `MSYS_NO_PATHCONV=1` 금지.
- 커밋 메시지는 한글 서술형, 접두사 없음, 끝에 `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.
- **하위 에이전트는 커밋·stage·stash·push 를 하지 않는다.** 커밋은 컨트롤러가 한다. 전체 `uv run pytest` 는 컨트롤러가 마지막에 한 번(에이전트는 자기 태스크 테스트만).
- 결정 번호는 **D-078** 로 쓴다. 착수 절차에서 `git log --all --oneline --grep="예약"` 과 `git log --all --oneline -S "## D-078" -- docs/decisions.md` 로 비었는지 확인하고 `chore: 착수 — D-078 예약 (#544)` 커밋으로 잡는다. 이미 쓰였으면 다음 빈 번호로 이 문서의 D-078 을 전부 바꾼다.

---

### Task 1: `daengs_cardgen` 서비스 뼈대 — 모델 프로토콜 · FastAPI 앱 · 전용 그룹 · 경계 테스트

**Files:**
- Create: `backend/src/daengs_cardgen/__init__.py`
- Create: `backend/src/daengs_cardgen/models.py`
- Create: `backend/src/daengs_cardgen/app.py`
- Modify: `backend/pyproject.toml` (`uv add --group cardgen …` 과 `module-name` 목록 한 줄)
- Modify: `backend/uv.lock` (uv 가 갱신)
- Test: `backend/tests/test_cardgen_app.py`, `backend/tests/test_cardgen_boundary.py`

**Interfaces:**
- Produces: `daengs_cardgen.models` — `EditRequest(images: list[PIL.Image.Image], prompt: str, seed: int, width: int, height: int, steps: int | None = None, guidance: float | None = None)` (frozen dataclass), `CardGenModel` Protocol(`name: str`, `load() -> None`, `edit(req: EditRequest) -> PIL.Image.Image`), `MAX_IMAGES = 4`, `snap(value: int) -> int`(16의 배수로 반올림, 최소 16)
- Produces: `daengs_cardgen.app` — `create_app(model: CardGenModel | None = None) -> FastAPI`, 모듈 전역 `app = create_app()`. `model` 이 `None` 이면 lifespan 에서 `daengs_cardgen.diffusion.model_by_name(os.environ["CARDGEN_MODEL"])` 로 만들고 `load()` 한다(그 모듈은 Task 2 가 만든다 — 이 Task 의 테스트는 항상 모델을 주입한다)

- [ ] **Step 1: 의존성 그룹을 만든다**

Run (in `backend/`):
```powershell
uv add --group cardgen "fastapi[standard]>=0.141.1" "pillow>=10.2"
```
Expected: `pyproject.toml` 의 `[dependency-groups]` 에 `cardgen = [...]` 가 생기고 `uv.lock` 이 갱신된다. 그룹 바로 위에 주석을 **Edit 도구로** 붙인다:

```toml
# 도감 카드 생성 GPU 서비스(D-078)가 받는 것 전부. **base 의존성을 안 받는다** —
# `docker/cardgen/Dockerfile` 이 `uv sync --frozen --only-group cardgen --no-install-project` 로 깐다
# (`realtime` 그룹과 같은 이유). torch 는 여기서도 리눅스 CPU 판으로 잠기고, CUDA 판은 Dockerfile 이
# 덮어쓴다 — `[tool.uv.sources]` 를 풀지 말 것.
# ⚠ 이름을 바꾸면 `docker/cardgen/Dockerfile` 의 `--only-group` 인자도 같이 고쳐라.
```

그리고 `[tool.uv.build-backend] module-name` 목록에 `"daengs_cardgen",` 을 `"daengs_cardimage",` 다음 줄에 넣는다(알파벳 순).

- [ ] **Step 2: 실패하는 경계 테스트를 쓴다**

```python
# backend/tests/test_cardgen_boundary.py
"""`daengs_cardgen` 은 GPU 이미지에 혼자 들어간다 (D-078).

- backend·cardimage·life·DB 를 import 하지 않는다 — 이미지에는 이 패키지만 복사된다.
- torch·diffusers 같은 무거운 것은 **함수 안에서만** import 한다 — 개발 PC 의 pytest 가
  이 패키지를 import 하는 것만으로 CUDA 스택을 끌어오지 않게.
- 반대로 backend·cardimage 도 이 패키지를 import 하지 않는다 — backend 이미지에 torch 가
  새어 들어가는 길을 막는 것이 패키지를 나눈 이유다.
"""

import ast
import tomllib
from pathlib import Path

BACKEND = Path(__file__).parents[1]
SRC = BACKEND / "src"
FORBIDDEN = {"daengs_backend", "daengs_cardimage", "daengs_life", "sqlalchemy"}
HEAVY = {"torch", "diffusers", "transformers", "bitsandbytes", "accelerate"}


def _names(nodes):
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module


def _all_imports(path: Path):
    return _names(ast.walk(ast.parse(path.read_text(encoding="utf-8"))))


def _module_level_imports(path: Path):
    return _names(ast.parse(path.read_text(encoding="utf-8")).body)


def test_cardgen_does_not_import_backend_cardimage_or_db() -> None:
    offenders = [
        f"{p.name}: {name}"
        for p in sorted((SRC / "daengs_cardgen").rglob("*.py"))
        for name in _all_imports(p)
        if name.split(".")[0] in FORBIDDEN
    ]
    assert offenders == []


def test_heavy_libraries_are_imported_only_inside_functions() -> None:
    offenders = [
        f"{p.name}: {name}"
        for p in sorted((SRC / "daengs_cardgen").rglob("*.py"))
        for name in _module_level_imports(p)
        if name.split(".")[0] in HEAVY
    ]
    assert offenders == []


def test_backend_side_never_imports_cardgen() -> None:
    offenders = [
        f"{p.relative_to(SRC)}: {name}"
        for pkg in ("daengs_backend", "daengs_cardimage")
        for p in sorted((SRC / pkg).rglob("*.py"))
        for name in _all_imports(p)
        if name.split(".")[0] == "daengs_cardgen"
    ]
    assert offenders == []


def test_cardgen_is_packaged_and_has_its_own_group() -> None:
    cfg = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))
    assert "daengs_cardgen" in cfg["tool"]["uv"]["build-backend"]["module-name"]
    names = {
        spec.split(";")[0].split(">=")[0].split("==")[0].split("<")[0].split("[")[0].strip()
        for spec in cfg["dependency-groups"]["cardgen"]
    }
    assert {"fastapi", "pillow"} <= names
```

- [ ] **Step 3: 실패하는 앱 테스트를 쓴다**

```python
# backend/tests/test_cardgen_app.py
"""GPU 서비스의 HTTP 계약 (D-078). 실제 모델은 부르지 않는다 — 가짜 모델을 주입한다."""

import base64
import io

from fastapi.testclient import TestClient
from PIL import Image

from daengs_cardgen.app import create_app
from daengs_cardgen.models import EditRequest, snap


class FakeModel:
    name = "fake"

    def __init__(self) -> None:
        self.requests: list[EditRequest] = []

    def load(self) -> None:
        raise AssertionError("주입한 모델은 lifespan 이 다시 올리지 않는다")

    def edit(self, req: EditRequest) -> Image.Image:
        self.requests.append(req)
        return Image.new("RGB", (req.width, req.height), (1, 2, 3))


def _b64(size=(64, 64), fmt="PNG") -> str:
    buf = io.BytesIO()
    Image.new("RGB", size, (9, 9, 9)).save(buf, fmt)
    return base64.b64encode(buf.getvalue()).decode()


def _body(**over) -> dict:
    body = {"images_b64": [_b64(), _b64(fmt="JPEG")], "prompt": "p", "seed": 7, "width": 994, "height": 1582}
    body.update(over)
    return body


def test_snap_rounds_to_multiple_of_16() -> None:
    assert snap(994) == 992
    assert snap(1582) == 1584
    assert snap(1024) == 1024
    assert snap(3) == 16


def test_health_reports_injected_model() -> None:
    with TestClient(create_app(model=FakeModel())) as client:
        assert client.get("/health").json() == {"model": "fake", "ready": True, "load_seconds": None}


def test_health_path_is_not_healthz() -> None:
    paths = {getattr(route, "path", None) for route in create_app(model=FakeModel()).routes}
    assert "/health" in paths
    assert "/healthz" not in paths  # Cloud Run 앞 구글 프런트엔드가 가로챈다 (D-070)


def test_generate_returns_png_at_snapped_size_and_passes_request() -> None:
    fake = FakeModel()
    with TestClient(create_app(model=fake)) as client:
        response = client.post("/generate", json=_body())
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["X-Cardgen-Model"] == "fake"
    assert response.headers["X-Cardgen-Size"] == "992x1584"
    assert float(response.headers["X-Cardgen-Seconds"]) >= 0
    assert Image.open(io.BytesIO(response.content)).size == (992, 1584)
    req = fake.requests[0]
    assert (req.seed, req.prompt, len(req.images), req.steps, req.guidance) == (7, "p", 2, None, None)


def test_generate_rejects_bytes_that_are_not_an_image() -> None:
    with TestClient(create_app(model=FakeModel())) as client:
        response = client.post("/generate", json=_body(images_b64=[base64.b64encode(b"nope").decode()]))
    assert response.status_code == 400
    assert response.json()["code"] == "bad_image"


def test_generate_validates_seed_and_image_count() -> None:
    with TestClient(create_app(model=FakeModel())) as client:
        assert client.post("/generate", json=_body(seed=-1)).status_code == 422
        assert client.post("/generate", json=_body(images_b64=[_b64()] * 5)).status_code == 422
```

- [ ] **Step 4: 테스트가 실패하는지 본다**

Run: `uv run pytest tests/test_cardgen_boundary.py tests/test_cardgen_app.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'daengs_cardgen'` (경계 테스트는 `daengs_cardgen` 폴더가 없어 빈 목록으로 통과하는 것도 있을 수 있다 — 앱 테스트가 실패하면 된다)

- [ ] **Step 5: 패키지를 만든다**

```python
# backend/src/daengs_cardgen/__init__.py
"""도감 카드 생성 GPU 서비스 (D-078).

Cloud Run L4 에서 diffusers 로 편집 모델 하나를 올리고 `POST /generate` 로 카드를 만든다.
backend 는 이 패키지를 import 하지 않고 HTTP 로만 부른다(`daengs_cardimage.engine.HttpCardImageEngine`).
이 패키지도 backend·cardimage 를 import 하지 않는다 — GPU 이미지에는 이 폴더만 들어간다.
torch·diffusers 는 함수 안에서만 import 한다 (`tests/test_cardgen_boundary.py`).
"""
```

```python
# backend/src/daengs_cardgen/models.py
"""모델 하나가 지켜야 할 모양과 요청. 실제 diffusers 구현은 `diffusion.py`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from PIL import Image

#: 한 요청의 이미지 수 상한. 우리는 틀·사진 두 장만 쓴다 — klein 호스팅 API 의 상한(4)에 맞췄다.
MAX_IMAGES = 4
SIZE_STEP = 16


@dataclass(frozen=True)
class EditRequest:
    images: list[Image.Image]
    prompt: str
    seed: int
    width: int
    height: int
    steps: int | None = None
    guidance: float | None = None


class CardGenModel(Protocol):
    name: str

    def load(self) -> None: ...

    def edit(self, req: EditRequest) -> Image.Image: ...


def snap(value: int) -> int:
    """diffusers 파이프라인은 16의 배수 크기를 요구한다. 가장 가까운 배수로(최소 16)."""
    return max(SIZE_STEP, round(value / SIZE_STEP) * SIZE_STEP)
```

```python
# backend/src/daengs_cardgen/app.py
"""GPU 서비스 HTTP 앱. 모델은 기동 때 한 번 올리고(lifespan), 요청은 한 번에 하나씩 처리한다.

기동에서 가중치를 다 올린 뒤에야 포트가 열린다 — Cloud Run 은 그동안 들어온 요청을 붙잡아 두므로
콜드 스타트 요청은 503 이 아니라 **느리게 성공**한다. 그 시간이 `/health` 의 `load_seconds` 다.
"""

from __future__ import annotations

import base64
import binascii
import io
import os
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from daengs_cardgen.models import MAX_IMAGES, CardGenModel, EditRequest, snap


class GenerateBody(BaseModel):
    images_b64: list[str] = Field(min_length=1, max_length=MAX_IMAGES)
    prompt: str = Field(min_length=1, max_length=4000)
    seed: int = Field(ge=0, le=2**31 - 1)
    width: int = Field(ge=256, le=2048)
    height: int = Field(ge=256, le=2048)
    steps: int | None = Field(default=None, ge=1, le=100)
    guidance: float | None = Field(default=None, ge=0, le=20)


def _decode(b64: str) -> Image.Image:
    try:
        return Image.open(io.BytesIO(base64.b64decode(b64, validate=True))).convert("RGB")
    except (binascii.Error, UnidentifiedImageError, OSError) as exc:
        raise ValueError(str(exc)) from exc


def create_app(model: CardGenModel | None = None) -> FastAPI:
    state: dict[str, Any] = {"model": model, "load_seconds": None}
    lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if state["model"] is None:
            from daengs_cardgen.diffusion import model_by_name

            started = time.monotonic()
            loaded = model_by_name(os.environ["CARDGEN_MODEL"])
            loaded.load()
            state["model"] = loaded
            state["load_seconds"] = round(time.monotonic() - started, 1)
            print(f"cardgen model={loaded.name} load_seconds={state['load_seconds']}", flush=True)
        yield

    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, Any]:
        current = state["model"]
        return {
            "model": getattr(current, "name", None),
            "ready": current is not None,
            "load_seconds": state["load_seconds"],
        }

    @app.post("/generate")
    def generate(body: GenerateBody) -> Response:
        current = state["model"]
        if current is None:
            return JSONResponse({"code": "not_ready", "message": "모델을 올리는 중입니다"}, status_code=503)
        try:
            images = [_decode(item) for item in body.images_b64]
        except ValueError as exc:
            return JSONResponse({"code": "bad_image", "message": f"이미지를 읽을 수 없습니다: {exc}"}, status_code=400)
        req = EditRequest(
            images=images, prompt=body.prompt, seed=body.seed,
            width=snap(body.width), height=snap(body.height), steps=body.steps, guidance=body.guidance,
        )
        started = time.monotonic()
        with lock:
            out = current.edit(req)
        buf = io.BytesIO()
        out.save(buf, "PNG")
        return Response(
            buf.getvalue(),
            media_type="image/png",
            headers={
                "X-Cardgen-Model": current.name,
                "X-Cardgen-Seconds": f"{time.monotonic() - started:.1f}",
                "X-Cardgen-Size": f"{req.width}x{req.height}",
            },
        )

    return app


app = create_app()
```

- [ ] **Step 6: 테스트가 통과하는지 본다**

Run: `uv run pytest tests/test_cardgen_boundary.py tests/test_cardgen_app.py tests/test_cardimage_boundary.py -v`
Expected: PASS (전부)

- [ ] **Step 7: 저장소 규칙 검사**

Run: `uv run check`
Expected: 통과

- [ ] **Step 8: 커밋 (컨트롤러)**

```powershell
git add backend/src/daengs_cardgen backend/tests/test_cardgen_app.py backend/tests/test_cardgen_boundary.py backend/pyproject.toml backend/uv.lock
git commit -m "카드 생성 GPU 서비스 패키지의 뼈대와 HTTP 계약을 세운다`n`nCo-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: diffusers 모델 두 개 — FLUX.2-klein-4B · Qwen 2511(nf4) · 가중치 받기

**Files:**
- Create: `backend/src/daengs_cardgen/diffusion.py`
- Create: `backend/src/daengs_cardgen/fetch.py`
- Modify: `backend/pyproject.toml`, `backend/uv.lock` (`uv add --group cardgen …`)
- Modify: `backend/tests/test_cardgen_boundary.py` (그룹 단언 한 줄)
- Test: `backend/tests/test_cardgen_diffusion.py`

**Interfaces:**
- Consumes: `daengs_cardgen.models.EditRequest`, `CardGenModel`
- Produces: `daengs_cardgen.diffusion` — `MODEL_REPOS: dict[str, str]` (`{"klein-4b": "black-forest-labs/FLUX.2-klein-4B", "qwen-edit-2511": "Qwen/Qwen-Image-Edit-2511"}`), `KleinModel`(`name = "klein-4b"`), `QwenModel(quant: str | None = None)`(`name = "qwen-edit-2511"`, `quant` 은 `"nf4"` 또는 `"none"`, 기본은 환경 변수 `CARDGEN_QWEN_QUANT` 또는 `"nf4"`), `resolve(req: EditRequest, defaults: dict[str, float]) -> tuple[int, float]`, `model_by_name(name: str) -> CardGenModel`(모르는 이름은 `ValueError`)
- Produces: `python -m daengs_cardgen.fetch [이름 …]` — `MODEL_REPOS` 의 저장소를 `HF_HOME` 에 받는다(인자 없으면 둘 다)

- [ ] **Step 1: 의존성을 더한다**

Run (in `backend/`):
```powershell
uv add --group cardgen "diffusers>=0.40" "transformers<5" "accelerate>=1.0" "bitsandbytes>=0.45" "torch==2.13.0" "huggingface-hub>=0.34"
```
Expected: 해석 성공. **실패하면**(예: diffusers 가 transformers 5 를 요구) 버전을 바꿔 가며 우회하지 말고 멈춰서 오류 전문을 컨트롤러에게 보고한다 — `ml` 그룹이 `transformers<5` 를 요구해서 lock 이 두 버전을 동시에 못 가진다.

- [ ] **Step 2: 파이프라인 인자 이름을 실제 설치본에서 확인한다**

Run:
```powershell
uv run --group cardgen python -c "import inspect, diffusers; print(diffusers.__version__); print(inspect.signature(diffusers.Flux2KleinPipeline.__call__)); print(inspect.signature(diffusers.QwenImageEditPlusPipeline.__call__))"
```
Expected: 두 시그니처에 `image`, `prompt`, `height`, `width`, `num_inference_steps`, `generator` 가 있고, klein 에 `guidance_scale`, Qwen 에 `true_cfg_scale`·`negative_prompt` 가 있다. **이름이 다르면** Step 5 코드의 해당 키워드만 실제 이름으로 바꾸고, 바꾼 것을 커밋 메시지 본문에 적는다.

- [ ] **Step 3: 경계 테스트의 그룹 단언을 넓힌다**

`backend/tests/test_cardgen_boundary.py` 의 마지막 줄을 바꾼다:

```python
    assert {"fastapi", "pillow", "diffusers", "transformers", "accelerate", "bitsandbytes", "torch", "huggingface-hub"} <= names
```

- [ ] **Step 4: 실패하는 테스트를 쓴다**

```python
# backend/tests/test_cardgen_diffusion.py
"""모델 선택과 기본값 (D-078). GPU·가중치 없이 도는 부분만 — 실제 추론은 Cloud Run 에서 확인한다."""

import pytest
from PIL import Image

from daengs_cardgen.diffusion import (
    KLEIN_DEFAULTS,
    MODEL_REPOS,
    QWEN_DEFAULTS,
    KleinModel,
    QwenModel,
    model_by_name,
    resolve,
)
from daengs_cardgen.models import EditRequest


def _req(**over) -> EditRequest:
    base = {"images": [Image.new("RGB", (8, 8))], "prompt": "p", "seed": 1, "width": 1024, "height": 1632}
    base.update(over)
    return EditRequest(**base)


def test_model_by_name_knows_exactly_the_two_candidates() -> None:
    assert set(MODEL_REPOS) == {"klein-4b", "qwen-edit-2511"}
    assert isinstance(model_by_name("klein-4b"), KleinModel)
    assert isinstance(model_by_name("qwen-edit-2511"), QwenModel)
    with pytest.raises(ValueError, match="klein-4b"):
        model_by_name("joyai")


def test_resolve_uses_model_defaults_unless_request_overrides() -> None:
    assert resolve(_req(), KLEIN_DEFAULTS) == (4, 1.0)
    assert resolve(_req(), QWEN_DEFAULTS) == (40, 4.0)
    assert resolve(_req(steps=28, guidance=4.5), QWEN_DEFAULTS) == (28, 4.5)


def test_qwen_quant_comes_from_env_and_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CARDGEN_QWEN_QUANT", raising=False)
    assert QwenModel().quant == "nf4"
    monkeypatch.setenv("CARDGEN_QWEN_QUANT", "none")
    assert QwenModel().quant == "none"
    with pytest.raises(ValueError, match="nf4"):
        QwenModel(quant="fp8")


def test_edit_before_load_is_a_clear_error() -> None:
    with pytest.raises(RuntimeError, match="load"):
        KleinModel().edit(_req())
```

- [ ] **Step 5: 실패하는지 본다**

Run: `uv run pytest tests/test_cardgen_diffusion.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'daengs_cardgen.diffusion'`

- [ ] **Step 6: 구현한다**

```python
# backend/src/daengs_cardgen/diffusion.py
"""diffusers 편집 파이프라인 두 개 (D-078). torch·diffusers 는 `load()`·`edit()` 안에서만 import 한다.

Qwen-Image-Edit-2511 은 bf16 가 약 40GB(transformer 20B + Qwen2.5-VL 7B)라 L4(24GB)에 그대로 안 들어간다.
기본은 transformer·text_encoder 를 bitsandbytes nf4 로 올린다(`CARDGEN_QWEN_QUANT=nf4`). `none` 은
96GB GPU(RTX PRO 6000)에서 bf16 으로 비교할 때만 쓴다. FLUX.2-klein-4B 는 약 13GB 라 bf16 그대로다.
"""

from __future__ import annotations

import os
from typing import Any

from PIL import Image

from daengs_cardgen.models import CardGenModel, EditRequest

MODEL_REPOS = {
    "klein-4b": "black-forest-labs/FLUX.2-klein-4B",
    "qwen-edit-2511": "Qwen/Qwen-Image-Edit-2511",
}
KLEIN_DEFAULTS = {"steps": 4, "guidance": 1.0}      # 증류판 권장값 (모델 카드)
QWEN_DEFAULTS = {"steps": 40, "guidance": 4.0}      # true_cfg_scale (모델 카드). Lightning 4-step 은 일러스트 품질 저하 보고가 있어 안 쓴다
QWEN_QUANTS = ("nf4", "none")


def resolve(req: EditRequest, defaults: dict[str, float]) -> tuple[int, float]:
    steps = req.steps if req.steps is not None else int(defaults["steps"])
    guidance = req.guidance if req.guidance is not None else float(defaults["guidance"])
    return steps, guidance


class KleinModel:
    name = "klein-4b"

    def __init__(self) -> None:
        self._pipe: Any = None

    def load(self) -> None:
        import torch
        from diffusers import Flux2KleinPipeline

        self._pipe = Flux2KleinPipeline.from_pretrained(
            MODEL_REPOS[self.name], torch_dtype=torch.bfloat16
        ).to("cuda")

    def edit(self, req: EditRequest) -> Image.Image:
        if self._pipe is None:
            raise RuntimeError("load() 를 먼저 불러야 합니다")
        import torch

        steps, guidance = resolve(req, KLEIN_DEFAULTS)
        result = self._pipe(
            image=req.images, prompt=req.prompt, width=req.width, height=req.height,
            num_inference_steps=steps, guidance_scale=guidance,
            generator=torch.Generator("cuda").manual_seed(req.seed),
        )
        return result.images[0]


class QwenModel:
    name = "qwen-edit-2511"

    def __init__(self, quant: str | None = None) -> None:
        self.quant = quant or os.environ.get("CARDGEN_QWEN_QUANT", "nf4")
        if self.quant not in QWEN_QUANTS:
            raise ValueError(f"CARDGEN_QWEN_QUANT 는 {QWEN_QUANTS} 중 하나입니다: {self.quant!r}")
        self._pipe: Any = None

    def load(self) -> None:
        import torch
        from diffusers import QwenImageEditPlusPipeline

        kwargs: dict[str, Any] = {"torch_dtype": torch.bfloat16}
        if self.quant == "nf4":
            from diffusers.quantizers import PipelineQuantizationConfig

            kwargs["quantization_config"] = PipelineQuantizationConfig(
                quant_backend="bitsandbytes_4bit",
                quant_kwargs={
                    "load_in_4bit": True,
                    "bnb_4bit_quant_type": "nf4",
                    "bnb_4bit_compute_dtype": torch.bfloat16,
                },
                components_to_quantize=["transformer", "text_encoder"],
            )
        self._pipe = QwenImageEditPlusPipeline.from_pretrained(MODEL_REPOS[self.name], **kwargs).to("cuda")

    def edit(self, req: EditRequest) -> Image.Image:
        if self._pipe is None:
            raise RuntimeError("load() 를 먼저 불러야 합니다")
        import torch

        steps, guidance = resolve(req, QWEN_DEFAULTS)
        result = self._pipe(
            image=req.images, prompt=req.prompt, negative_prompt=" ",
            width=req.width, height=req.height,
            num_inference_steps=steps, true_cfg_scale=guidance,
            generator=torch.Generator("cuda").manual_seed(req.seed),
        )
        return result.images[0]


MODELS: dict[str, type] = {KleinModel.name: KleinModel, QwenModel.name: QwenModel}


def model_by_name(name: str) -> CardGenModel:
    try:
        factory = MODELS[name]
    except KeyError:
        raise ValueError(f"모르는 모델 {name!r} — 가능: {sorted(MODELS)}") from None
    return factory()
```

```python
# backend/src/daengs_cardgen/fetch.py
"""가중치를 `HF_HOME` 에 받아 둔다 — Cloud Run 잡 `cardgen-weights` 가 버킷을 /models 로 마운트하고 부른다.

서비스는 기동마다 인터넷에서 받지 않는다(`HF_HUB_OFFLINE=1`). 컨테이너 파일 시스템은 메모리라
20GB 넘는 가중치를 거기 받으면 인스턴스가 죽는다 — 그래서 버킷에 한 번만 받는다.
"""

from __future__ import annotations

import sys

from daengs_cardgen.diffusion import MODEL_REPOS


def main(argv: list[str] | None = None) -> int:
    names = list(argv if argv is not None else sys.argv[1:]) or sorted(MODEL_REPOS)
    unknown = [n for n in names if n not in MODEL_REPOS]
    if unknown:
        print(f"모르는 모델 {unknown} — 가능: {sorted(MODEL_REPOS)}", file=sys.stderr)
        return 2
    from huggingface_hub import snapshot_download

    for name in names:
        path = snapshot_download(MODEL_REPOS[name])
        print(f"fetched {name} -> {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 7: 통과하는지 본다**

Run: `uv run pytest tests/test_cardgen_diffusion.py tests/test_cardgen_boundary.py tests/test_cardgen_app.py -v`
Expected: PASS

- [ ] **Step 8: 가중치 받기의 인자 검사를 손으로 본다 (다운로드 없이)**

Run: `uv run --group cardgen python -m daengs_cardgen.fetch joyai; echo "exit=$LASTEXITCODE"`
Expected: `모르는 모델 ['joyai'] …` 와 `exit=2`

- [ ] **Step 9: 커밋 (컨트롤러)**

```powershell
git add backend/src/daengs_cardgen backend/tests/test_cardgen_diffusion.py backend/tests/test_cardgen_boundary.py backend/pyproject.toml backend/uv.lock
git commit -m "Qwen-Image-Edit-2511 과 FLUX.2-klein-4B 파이프라인과 가중치 받기를 붙인다`n`nCo-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: backend 쪽 — HTTP 엔진 · `ai_card_engine` 갈림길 · 설정

**Files:**
- Modify: `backend/src/daengs_cardimage/engine.py` (HTTP 엔진 추가)
- Modify: `backend/src/daengs_backend/services/realtime_client.py` (공개 함수 `id_token` 한 개)
- Modify: `backend/src/daengs_backend/services/ai_card_engine.py`
- Modify: `backend/src/daengs_backend/config.py` (설정 두 개)
- Modify: `backend/.env.example`
- Test: `backend/tests/test_cardimage_engine_http.py` (새 파일), `backend/tests/test_ai_card_engine.py`, `backend/tests/test_cardimage_settings.py`

**Interfaces:**
- Consumes: 서비스 계약(Global Constraints), `daengs_cardimage.engine.CARD_SIZE`, `EngineError(code, detail)`, `_decode_and_fit(image_bytes, *, pad, padded_width)`
- Produces: `daengs_cardimage.engine.GEN_SIZE = (1024, 1632)`, `HttpCardImageEngine(*, base_url: str, timeout_s: float, seed: int | None = None, auth: Callable[[str], str] | None = None, transport: httpx.BaseTransport | None = None)` — `generate(*, template_png, photo_jpeg, prompt) -> bytes`(994×1582 PNG), 속성 `last_meta: dict | None`(`{"seed": int, "seconds": str | None, "model": str | None}`)
- Produces: `daengs_backend.config.settings.cardgen_url: str`(기본 `""`, `DAENGS_CARDGEN_URL`), `settings.cardgen_timeout_s: float`(기본 `900.0`, `DAENGS_CARDGEN_TIMEOUT_S`)
- Produces: `daengs_backend.services.realtime_client.id_token(audience: str) -> str`

- [ ] **Step 1: 실패하는 HTTP 엔진 테스트를 쓴다**

```python
# backend/tests/test_cardimage_engine_http.py
"""`HttpCardImageEngine` — GPU 카드 생성 서비스(D-078)를 부르는 엔진. 네트워크 없이 MockTransport 로."""

import base64
import io
import json

import httpx
import pytest
from cardimage_fakes import png
from PIL import Image

from daengs_cardimage.engine import GEN_SIZE, EngineError, HttpCardImageEngine


def _engine(handler, **over) -> HttpCardImageEngine:
    kwargs = {"base_url": "https://cardgen.example/", "timeout_s": 5, "seed": 11,
              "auth": lambda audience: f"tok:{audience}", "transport": httpx.MockTransport(handler)}
    kwargs.update(over)
    return HttpCardImageEngine(**kwargs)


def test_posts_template_then_photo_and_fits_card_size() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=png(*GEN_SIZE),
                              headers={"X-Cardgen-Seconds": "3.2", "X-Cardgen-Model": "fake"})

    engine = _engine(handler)
    template = png(994, 1582, (1, 1, 1))
    out = engine.generate(template_png=template, photo_jpeg=b"jpeg-bytes", prompt="P")

    assert Image.open(io.BytesIO(out)).size == (994, 1582)
    assert seen["url"] == "https://cardgen.example/generate"
    assert seen["auth"] == "Bearer tok:https://cardgen.example"
    body = seen["body"]
    assert (body["seed"], body["prompt"], body["width"], body["height"]) == (11, "P", 1024, 1632)
    assert base64.b64decode(body["images_b64"][0]) == template
    assert base64.b64decode(body["images_b64"][1]) == b"jpeg-bytes"
    assert engine.last_meta == {"seed": 11, "seconds": "3.2", "model": "fake"}


def test_without_auth_sends_no_authorization_header() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, content=png(*GEN_SIZE))

    _engine(handler, auth=None).generate(template_png=png(), photo_jpeg=b"j", prompt="P")
    assert seen["auth"] is None


def test_random_seed_when_not_fixed() -> None:
    seeds = []

    def handler(request: httpx.Request) -> httpx.Response:
        seeds.append(json.loads(request.content)["seed"])
        return httpx.Response(200, content=png(*GEN_SIZE))

    engine = _engine(handler, seed=None)
    engine.generate(template_png=png(), photo_jpeg=b"j", prompt="P")
    assert 0 <= seeds[0] < 2**31
    assert engine.last_meta["seed"] == seeds[0]


def test_non_200_is_upstream_error() -> None:
    engine = _engine(lambda request: httpx.Response(503, json={"code": "not_ready"}))
    with pytest.raises(EngineError) as info:
        engine.generate(template_png=png(), photo_jpeg=b"j", prompt="P")
    assert info.value.code == "upstream"
    assert "503" in info.value.detail


def test_transport_failure_is_upstream_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(EngineError) as info:
        _engine(handler).generate(template_png=png(), photo_jpeg=b"j", prompt="P")
    assert info.value.code == "upstream"


def test_empty_url_is_no_key() -> None:
    engine = _engine(lambda request: httpx.Response(200), base_url="  ")
    with pytest.raises(EngineError) as info:
        engine.generate(template_png=png(), photo_jpeg=b"j", prompt="P")
    assert info.value.code == "no_key"


def test_non_image_body_is_no_image() -> None:
    engine = _engine(lambda request: httpx.Response(200, content=b"not a png"))
    with pytest.raises(EngineError) as info:
        engine.generate(template_png=png(), photo_jpeg=b"j", prompt="P")
    assert info.value.code == "no_image"
```

- [ ] **Step 2: 실패하는지 본다**

Run: `uv run pytest tests/test_cardimage_engine_http.py -v`
Expected: FAIL — `ImportError: cannot import name 'GEN_SIZE'`

- [ ] **Step 3: HTTP 엔진을 구현한다**

`backend/src/daengs_cardimage/engine.py` 의 import 블록을 바꾼다:

```python
from __future__ import annotations

import base64
import io
import random
from collections.abc import Callable
from typing import Protocol

import httpx
from PIL import Image, UnidentifiedImageError
```

`CARD_SIZE = (994, 1582)` 바로 아래에 넣는다:

```python
#: GPU 서비스(D-078)에 요청하는 생성 크기. 16의 배수이면서 카드 비율(0.628)에 가장 가깝다(0.627).
#: 받은 뒤 CARD_SIZE 로 줄인다 — 가로·세로 배율 차이는 0.1% 라 눈에 안 띈다.
GEN_SIZE = (1024, 1632)
```

파일 끝(`GeminiCardImageEngine` 아래)에 넣는다:

```python
class HttpCardImageEngine:
    """GPU 카드 생성 서비스(`daengs_cardgen`, D-078)를 부르는 엔진.

    인증은 `auth(audience) -> ID 토큰` 을 주입받는다 — 이 패키지는 backend 를 import 하지 않으므로
    토큰 발급(메타데이터 서버)은 backend 가 넘긴다. `auth=None` 이면 헤더 없이 부른다
    (`gcloud run services proxy` 로 연 로컬 포트 — 비교 도구가 쓴다).
    `seed=None` 이면 호출마다 무작위. 마지막 호출의 seed·서비스 시간·모델은 `last_meta` 에 남긴다.
    """

    def __init__(self, *, base_url: str, timeout_s: float, seed: int | None = None,
                 auth: Callable[[str], str] | None = None,
                 transport: httpx.BaseTransport | None = None) -> None:
        self._base = base_url.strip().rstrip("/")
        self._timeout_s, self._seed, self._auth, self._transport = timeout_s, seed, auth, transport
        self.last_meta: dict | None = None

    def generate(self, *, template_png: bytes, photo_jpeg: bytes, prompt: str) -> bytes:
        if not self._base:
            raise EngineError("no_key", "DAENGS_CARDGEN_URL 이 비어 있습니다")
        seed = self._seed if self._seed is not None else random.randrange(2**31)
        body = {
            "images_b64": [base64.b64encode(template_png).decode(), base64.b64encode(photo_jpeg).decode()],
            "prompt": prompt, "seed": seed, "width": GEN_SIZE[0], "height": GEN_SIZE[1],
        }
        try:
            headers = {"Authorization": f"Bearer {self._auth(self._base)}"} if self._auth else {}
            with httpx.Client(timeout=self._timeout_s, transport=self._transport) as client:
                resp = client.post(f"{self._base}/generate", json=body, headers=headers)
        except Exception as exc:  # 토큰 발급 실패까지 "응답을 못 받은" 것으로 모은다 (realtime_client 와 같은 판단)
            raise EngineError("upstream", f"카드 생성 서비스 호출 실패: {type(exc).__name__}: {exc}") from exc
        if resp.status_code != 200:
            raise EngineError("upstream", f"카드 생성 서비스가 {resp.status_code} 을 돌려줬습니다: {resp.text[:200]!r}")
        self.last_meta = {"seed": seed, "seconds": resp.headers.get("X-Cardgen-Seconds"),
                          "model": resp.headers.get("X-Cardgen-Model")}
        return _decode_and_fit(resp.content, pad=0, padded_width=CARD_SIZE[0])
```

- [ ] **Step 4: HTTP 엔진 테스트가 통과하는지 본다**

Run: `uv run pytest tests/test_cardimage_engine_http.py tests/test_cardimage_engine.py tests/test_cardimage_boundary.py -v`
Expected: PASS

- [ ] **Step 5: 설정·갈림길 테스트를 먼저 쓴다**

`backend/tests/test_cardimage_settings.py` 끝에 더한다:

```python
def test_cardgen_defaults_keep_gemini_path(monkeypatch):
    for k in ("DAENGS_CARDGEN_URL", "DAENGS_CARDGEN_TIMEOUT_S"):
        monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=None)
    assert s.cardgen_url == ""          # 비어 있으면 Nano Banana 2 (D-074) 그대로
    assert s.cardgen_timeout_s == 900.0  # 콜드 스타트(가중치 로드) + 생성
```

`backend/tests/test_ai_card_engine.py` 의 import 에 `from daengs_backend.services import ai_card_engine, realtime_client` 와 `from daengs_cardimage.engine import GeminiCardImageEngine, HttpCardImageEngine` 를 쓰고(기존 `from daengs_backend.services import ai_card_engine` 줄을 바꾼다), 끝에 더한다:

```python
def test_default_engine_is_gemini_when_cardgen_url_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cardgen_url", "")
    assert isinstance(ai_card_engine.default_engine(), GeminiCardImageEngine)


def test_default_engine_is_http_when_cardgen_url_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cardgen_url", " https://cardgen.example ")
    monkeypatch.setattr(settings, "cardgen_timeout_s", 30.0)
    engine = ai_card_engine.default_engine()
    assert isinstance(engine, HttpCardImageEngine)
    assert engine._base == "https://cardgen.example"
    assert engine._timeout_s == 30.0
    assert engine._auth is realtime_client.id_token


def test_ready_check_without_gemini_key_passes_when_cardgen_url_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cardimage_gemini_api_key", SecretStr(""))
    monkeypatch.setattr(settings, "cardgen_url", "https://cardgen.example")
    assert ai_card_engine.ready_check(4).month == 4
```

기존 `test_ready_check_without_key_is_unavailable` 첫 줄 앞에 `monkeypatch.setattr(settings, "cardgen_url", "")` 를 넣는다(개발 PC `.env` 에 값이 있어도 흔들리지 않게).

- [ ] **Step 6: 실패하는지 본다**

Run: `uv run pytest tests/test_cardimage_settings.py tests/test_ai_card_engine.py -v`
Expected: FAIL — `AttributeError: ... 'cardgen_url'`

- [ ] **Step 7: 설정을 더한다**

`backend/src/daengs_backend/config.py` 의 `cardimage_concurrency` 필드 바로 아래에 넣는다:

```python
    # GPU 카드 생성 서비스(D-078, Cloud Run asia-southeast1 L4). **비어 있으면 Nano Banana 2(D-074)
    # 그대로** — 되돌리기가 이 한 줄이다. ⚠ 앱 경로(`/app/ai-cards`)의 정리 기준은 아직
    # `cardimage_timeout_ms` 만 보므로 콜드 스타트(가중치 로드 수 분)를 모른다 — #544 에서는 VM 에 넣지 않는다.
    cardgen_url: str = Field(default="", validation_alias=AliasChoices("DAENGS_CARDGEN_URL"))
    # 콜드 스타트 + 생성. `infra/gcp/cardgen.sh` 의 `--timeout=900` 과 맞춘다.
    cardgen_timeout_s: float = Field(default=900.0, gt=0, validation_alias=AliasChoices("DAENGS_CARDGEN_TIMEOUT_S"))
```

`backend/.env.example` 의 `#DAENGS_CARDIMAGE_CONCURRENCY=2` 줄 아래에 넣는다:

```
# GPU 카드 생성 서비스 (D-078). 비어 있으면 Nano Banana 2 그대로입니다.
# ⚠ 앱 경로의 정리 기준이 콜드 스타트를 아직 모릅니다 — 서버에는 넣지 마세요 (#544 남은 것).
#DAENGS_CARDGEN_URL=
#DAENGS_CARDGEN_TIMEOUT_S=900
```

- [ ] **Step 8: 갈림길을 구현한다**

`backend/src/daengs_backend/services/realtime_client.py` 의 `_call` 정의 바로 위에 넣고, `__all__` 에 `"id_token"` 을 더한다:

```python
def id_token(audience: str) -> str:
    """다른 Cloud Run 서비스(카드 생성, D-078)도 같은 캐시로 토큰을 받게 여는 공개 이름.
    `_id_token` 을 호출 시점에 찾으므로 그 이름을 갈아끼우는 기존 테스트가 그대로 먹는다."""
    return _id_token(audience)
```

`backend/src/daengs_backend/services/ai_card_engine.py` 의 import 와 두 함수를 바꾼다:

```python
from daengs_backend.config import settings
from daengs_backend.services import realtime_client
from daengs_cardimage import CardImageUnavailable, GeneratedCard, catalog, generate_card
from daengs_cardimage.engine import CardImageEngine, GeminiCardImageEngine, HttpCardImageEngine
from daengs_cardimage.judge import CardJudge, GeminiCardJudge


def default_engine() -> CardImageEngine:
    """설정에서 실제 엔진을 만든다. `DAENGS_CARDGEN_URL` 이 있으면 GPU 서비스(D-078), 없으면
    Nano Banana 2 — D-070 의 `DAENGS_REALTIME_URL` 갈림길과 같은 모양이다.
    전역 `settings.gemini_api_key` 로 대체하지 않는다 — 카드 생성 키는 `DAENGS_CARDIMAGE_GEMINI_API_KEY` 하나뿐이다."""
    url = settings.cardgen_url.strip()
    if url:
        return HttpCardImageEngine(base_url=url, timeout_s=settings.cardgen_timeout_s,
                                   auth=realtime_client.id_token)
    return GeminiCardImageEngine(
        api_key=settings.cardimage_gemini_api_key.get_secret_value(),
        model=settings.cardimage_model,
        size=settings.cardimage_size,
        timeout_ms=settings.cardimage_timeout_ms,
    )
```

`ready_check` 의 키 확인 두 줄을 바꾼다:

```python
    if not settings.cardgen_url.strip() and not settings.cardimage_gemini_api_key.get_secret_value().strip():
        raise CardImageUnavailable("DAENGS_CARDIMAGE_GEMINI_API_KEY 가 비어 있습니다")
```

- [ ] **Step 9: 통과하는지 본다**

Run: `uv run pytest tests/test_cardimage_settings.py tests/test_ai_card_engine.py tests/test_cardimage_engine_http.py tests/test_realtime_service_split.py tests/test_cardgen_boundary.py -v`
Expected: PASS

- [ ] **Step 10: 커밋 (컨트롤러)**

```powershell
git add backend/src/daengs_cardimage/engine.py backend/src/daengs_backend/services/ai_card_engine.py backend/src/daengs_backend/services/realtime_client.py backend/src/daengs_backend/config.py backend/.env.example backend/tests/test_cardimage_engine_http.py backend/tests/test_ai_card_engine.py backend/tests/test_cardimage_settings.py
git commit -m "카드 생성 엔진을 URL 하나로 GPU 서비스와 Nano Banana 2 사이에서 고르게 한다`n`nCo-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: 비교 판정 — 틀 밀림 · 제목판 측정 · 비교 도구

**Files:**
- Create: `backend/src/daengs_cardimage/drift.py`
- Modify: `backend/src/daengs_cardimage/title.py` (공개 함수 `plate_shift`)
- Create: `backend/tools/cardgen_compare.py`
- Test: `backend/tests/test_cardimage_drift.py`

**Interfaces:**
- Consumes: `HttpCardImageEngine`, `GeminiCardImageEngine`, `GeminiCardJudge`, `generate_card(..., judge_min=1)`(재시도 없음), `catalog.template_path`, `catalog.get(month).plate`
- Produces: `daengs_cardimage.drift.Drift(dx: int, dy: int, mad_at_zero: float, mad_at_best: float)`, `frame_drift(template: Image.Image, card: Image.Image, *, band: int = 24, max_shift: int = 8, scale: int = 2) -> Drift` — 출력 내용이 오른쪽·아래로 밀렸으면 `dx`·`dy` 가 양수
- Produces: `daengs_cardimage.title.plate_shift(card: Image.Image, plate: Plate = APRIL_PLATE) -> int`
- Produces: `backend/tools/cardgen_compare.py` — `<out>/<사진>_<달>_s<seed>.png` 와 `<out>/results.jsonl`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

```python
# backend/tests/test_cardimage_drift.py
"""틀 밀림·제목판 측정 (D-078 비교 판정). Qwen 계열의 "image drift" 를 숫자로 잡는 자리다."""

from PIL import Image, ImageChops

from daengs_backend.config import settings
from daengs_cardimage import catalog
from daengs_cardimage.drift import frame_drift
from daengs_cardimage.title import plate_shift


def _noise() -> Image.Image:
    return Image.effect_noise((994, 1582), 64).convert("RGB")


def test_identical_card_has_no_drift() -> None:
    tpl = _noise()
    drift = frame_drift(tpl, tpl.copy())
    assert (drift.dx, drift.dy) == (0, 0)
    assert drift.mad_at_zero == 0


def test_shifted_card_reports_the_shift() -> None:
    tpl = _noise()
    moved = ImageChops.offset(tpl, 4, -2)   # 내용이 오른쪽 4 · 위 2 로 밀림
    drift = frame_drift(tpl, moved)
    assert (drift.dx, drift.dy) == (4, -2)
    assert drift.mad_at_best < drift.mad_at_zero


def test_plate_shift_is_zero_on_template_and_follows_offset() -> None:
    card = catalog.get(4)
    tpl = Image.open(catalog.template_path(4, settings.cardimage_dir)).convert("RGB")
    assert plate_shift(tpl, card.plate) == 0
    assert plate_shift(ImageChops.offset(tpl, 0, 5), card.plate) == 5
```

- [ ] **Step 2: 실패하는지 본다**

Run: `uv run pytest tests/test_cardimage_drift.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'daengs_cardimage.drift'`

- [ ] **Step 3: 구현한다**

```python
# backend/src/daengs_cardimage/drift.py
"""모델 출력이 카드 틀을 얼마나 밀었는지 잰다 (D-078 비교 판정).

카드 바깥 테두리 띠만 본다 — 강아지·제목은 원래 달라야 하는 자리라 빼고, 테두리는 틀과 같아야 한다.
출력을 (dx, dy) 만큼 옮겨 봤을 때 틀과 가장 잘 맞는 이동량이 밀림이다. 반 해상도로 찾고 2배로 돌려준다.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageChops, ImageStat

Box = tuple[int, int, int, int]


@dataclass(frozen=True)
class Drift:
    dx: int
    dy: int
    mad_at_zero: float   # 옮기지 않았을 때 테두리 평균 절대 차이(0~255)
    mad_at_best: float   # 가장 잘 맞는 이동에서의 차이


def _band_boxes(w: int, h: int, band: int, margin: int) -> list[Box]:
    return [
        (margin, margin, w - margin, margin + band),
        (margin, h - margin - band, w - margin, h - margin),
        (margin, margin + band, margin + band, h - margin - band),
        (w - margin - band, margin + band, w - margin, h - margin - band),
    ]


def _mad(ref: Image.Image, img: Image.Image, dx: int, dy: int, boxes: list[Box]) -> float:
    total, area = 0.0, 0
    for x0, y0, x1, y1 in boxes:
        a = ref.crop((x0, y0, x1, y1))
        b = img.crop((x0 + dx, y0 + dy, x1 + dx, y1 + dy))
        n = (x1 - x0) * (y1 - y0)
        total += ImageStat.Stat(ImageChops.difference(a, b)).mean[0] * n
        area += n
    return total / area


def frame_drift(template: Image.Image, card: Image.Image, *, band: int = 24, max_shift: int = 8,
                scale: int = 2) -> Drift:
    size = (template.width // scale, template.height // scale)
    ref = template.convert("L").resize(size, Image.BILINEAR)
    img = card.convert("L").resize(size, Image.BILINEAR)
    reach = max_shift // scale
    boxes = _band_boxes(size[0], size[1], max(1, band // scale), reach + 1)
    zero = _mad(ref, img, 0, 0, boxes)
    candidates = (
        (_mad(ref, img, dx, dy, boxes), abs(dx) + abs(dy), dx, dy)
        for dy in range(-reach, reach + 1)
        for dx in range(-reach, reach + 1)
    )
    best_mad, _, dx, dy = min(candidates)
    return Drift(dx=dx * scale, dy=dy * scale, mad_at_zero=round(zero, 2), mad_at_best=round(best_mad, 2))
```

`backend/src/daengs_cardimage/title.py` 의 `_plate_shift` 정의 바로 아래에 넣는다:

```python
def plate_shift(card: Image.Image, plate: Plate = APRIL_PLATE) -> int:
    """비교 판정용 공개 이름 — 모델 출력의 제목판이 틀보다 몇 px 위(-)·아래(+)로 그려졌는지. 못 재면 0."""
    return _plate_shift(card.convert("RGB"), plate)
```

- [ ] **Step 4: 통과하는지 본다**

Run: `uv run pytest tests/test_cardimage_drift.py tests/test_cardimage_title.py tests/test_cardimage_boundary.py -v`
Expected: PASS

- [ ] **Step 5: 비교 도구를 쓴다**

```python
# backend/tools/cardgen_compare.py
"""GPU 카드 생성 서비스(D-078)와 Nano Banana 2 를 같은 사진·틀·seed 로 비교한다 (#544).

    uv run python tools/cardgen_compare.py --engine cardgen --url http://127.0.0.1:8091 \
        --photos ../cardimage/test/_03.jpg --months 4,9 --seeds 1,2 --out ../cardimage/out/_cardgen/klein
    uv run python tools/cardgen_compare.py --engine gemini --photos ... --out ../cardimage/out/_cardgen/gemini

결과: `<out>/<사진>_<달>_s<seed>.png` 와 `<out>/results.jsonl` 한 줄씩 — 닮음·글자·아바타(검수),
틀 밀림(`drift`), 제목판 어긋남(`plate_shift`), 걸린 시간(`seconds`, 서비스 쪽은 `service.seconds`).
재시도는 하지 않는다(`judge_min=1`) — 한 장 한 장이 비교 표본이다.

⚠ 돈이 나간다: gemini 엔진 장당 약 $0.10, 검수 장당 몇 원, cardgen 은 Cloud Run L4 가 떠 있는 시간.
**실행 전에 사람에게 장수·순서를 설명하고 승인받는다** (docs/cardimage/README).
`cardgen` 의 `--url` 은 `gcloud run services proxy <서비스> --region=asia-southeast1 --port=8091` 로 연 주소다.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

from PIL import Image

from daengs_backend.config import settings
from daengs_cardimage import catalog, generate_card
from daengs_cardimage.drift import frame_drift
from daengs_cardimage.engine import GeminiCardImageEngine, HttpCardImageEngine
from daengs_cardimage.judge import GeminiCardJudge

MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--engine", choices=("cardgen", "gemini"), required=True)
    parser.add_argument("--url", default="", help="cardgen 서비스 주소 (proxy 로 연 로컬 포트)")
    parser.add_argument("--photos", required=True, help="쉼표로 구분한 사진 경로")
    parser.add_argument("--months", default="4,9")
    parser.add_argument("--seeds", default="1")
    parser.add_argument("--dog-name", default="테스트")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    key = settings.cardimage_gemini_api_key.get_secret_value().strip()
    if not key:
        print("DAENGS_CARDIMAGE_GEMINI_API_KEY 가 필요합니다 (검수·gemini 엔진)", file=sys.stderr)
        return 2
    if args.engine == "cardgen" and not args.url:
        print("--engine cardgen 에는 --url 이 필요합니다", file=sys.stderr)
        return 2

    from daengs_cardimage.title import plate_shift

    judge = GeminiCardJudge(api_key=key, model=settings.cardimage_judge_model,
                            timeout_ms=settings.cardimage_timeout_ms)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    months = sorted(int(m) for m in args.months.split(","))
    seeds = [int(s) for s in args.seeds.split(",")]

    for photo_path in [Path(p) for p in args.photos.split(",")]:
        photo = photo_path.read_bytes()
        for month in months:
            template = Image.open(catalog.template_path(month, settings.cardimage_dir)).convert("RGB")
            for seed in seeds:
                if args.engine == "cardgen":
                    engine = HttpCardImageEngine(base_url=args.url, timeout_s=settings.cardgen_timeout_s, seed=seed)
                else:
                    engine = GeminiCardImageEngine(api_key=key, model=settings.cardimage_model,
                                                   size=settings.cardimage_size,
                                                   timeout_ms=settings.cardimage_timeout_ms)
                started = time.monotonic()
                card = generate_card(
                    photo=photo, content_type=MIME[photo_path.suffix.lower()], month=month,
                    dog_name=args.dog_name, engine=engine, judge=judge, base_dir=settings.cardimage_dir,
                    open_months=frozenset(months), judge_min=1,
                )
                seconds = round(time.monotonic() - started, 1)
                name = f"{photo_path.stem}_{month}_s{seed}"
                (out / f"{name}.png").write_bytes(card.png)
                image = Image.open(io.BytesIO(card.png)).convert("RGB")
                row = {
                    "name": name, "engine": args.engine, "photo": photo_path.name, "month": month, "seed": seed,
                    "seconds": seconds, "service": getattr(engine, "last_meta", None),
                    "likeness": card.judge.likeness if card.judge else None,
                    "text_ok": card.judge.text_ok if card.judge else None,
                    "avatar_ok": card.judge.avatar_ok if card.judge else None,
                    "judge_note": card.judge.note if card.judge else None,
                    "drift": asdict(frame_drift(template, image)),
                    "plate_shift": plate_shift(image, catalog.get(month).plate),
                }
                with (out / "results.jsonl").open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                print(json.dumps(row, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: 도구가 돈 안 쓰고 뜨는지만 본다**

Run (in `backend/`): `uv run python tools/cardgen_compare.py --help`
Expected: 사용법이 출력되고 종료 코드 0. **`--help` 말고는 실행하지 않는다** (Task 8 에서 승인 뒤).

- [ ] **Step 7: 커밋 (컨트롤러)**

```powershell
git add backend/src/daengs_cardimage/drift.py backend/src/daengs_cardimage/title.py backend/tools/cardgen_compare.py backend/tests/test_cardimage_drift.py
git commit -m "비교 판정으로 틀 밀림과 제목판 어긋남을 재고 비교 도구를 둔다`n`nCo-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: 이미지와 배포 스크립트 — `docker/cardgen/` · `infra/gcp/cardgen.sh` · teardown

**Files:**
- Create: `docker/cardgen/Dockerfile`
- Create: `docker/cardgen/Dockerfile.dockerignore`
- Modify: `.gcloudignore`
- Create: `infra/gcp/cardgen.sh`
- Create: `infra/gcp/cardgen-teardown.sh`
- Modify: `infra/gcp/README.md` (새 절)

**Interfaces:**
- Consumes: `daengs_cardgen.app:app`, `python -m daengs_cardgen.fetch`, 그룹 `cardgen`
- Produces: `MODEL=klein|qwen PROJECT=daengs INVOKER=user:<계정> bash infra/gcp/cardgen.sh` — 서비스 `daengs-cardgen-${MODEL}` URL 출력. `STEP=image|weights|deploy` 로 한 단계만 돌릴 수 있다(기본 전부)

- [ ] **Step 1: Dockerfile 과 dockerignore 를 쓴다**

```dockerfile
# docker/cardgen/Dockerfile
# 도감 카드 생성 GPU 서비스 (D-078) — Cloud Run L4 (asia-southeast1).
#
#   gcloud builds submit 은 infra/gcp/cardgen.sh 가 한다. 로컬 docker build 는 하지 않는다.
#
# **코드는 이미지에 굽는다** (docker/realtime 과 같은 이유 — 서비스는 "지금 도는 코드" 가 배포 단위와
# 같아야 롤백이 된다). **가중치는 굽지 않는다** — 20~57GB 라 빌드·풀·콜드 스타트가 전부 느려진다.
# 버킷을 /models 로 마운트하고 HF_HOME 이 그것을 본다(`python -m daengs_cardgen.fetch` 가 채운다).
#
# lock 의 torch 는 리눅스 CPU 판으로 잠겨 있다(backend/pyproject.toml [tool.uv.sources]). CUDA 판은
# **설치가 끝난 뒤** torch 만 cu126 인덱스에서 덮어쓴다 — 방법과 함정은 docker/pipeline/Dockerfile 주석을
# 그대로 따른다: `torch==X` 는 `+cpu` 에 만족돼 조용히 건너뛰므로 `+cu126` 을 박고,
# `uv sync --frozen` 은 이 파일에 한 번만 나온다(덮어쓰기 뒤에 부르면 CPU 판으로 되돌린다).
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
    PYTHONPATH=/app/src \
    HF_HOME=/models \
    HF_HUB_DISABLE_TELEMETRY=1

# torch 2.13 의 CUDA 경로는 Triton 커널을 실행 시점에 컴파일한다 — slim 에는 gcc 가 없다 (2026-09-08 L4 실측).
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*

COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN uv sync --frozen --only-group cardgen --no-install-project

# torchvision 도 같이 덮어쓴다 — transformers 5.x 의 Qwen2VLProcessor 가 torchvision 을 요구한다.
RUN set -eux; \
    V_TORCH="$(uv pip show --python /opt/venv/bin/python torch | awk '/^Version/ {print $2}' | cut -d+ -f1)"; \
    V_TV="$(uv pip show --python /opt/venv/bin/python torchvision | awk '/^Version/ {print $2}' | cut -d+ -f1)"; \
    uv pip install --python /opt/venv/bin/python --reinstall-package torch --reinstall-package torchvision \
        --index-url https://download.pytorch.org/whl/cu126 "torch==${V_TORCH}+cu126" "torchvision==${V_TV}+cu126"
# 빌드 머신에는 GPU 가 없어 is_available() 은 못 쓴다 — "CUDA 빌드인지"만 본다.
RUN /opt/venv/bin/python -c "import torch, torchvision, sys; print('torch', torch.__version__, 'cuda', torch.version.cuda, 'torchvision', torchvision.__version__); sys.exit(0 if torch.version.cuda else 1)"

COPY backend/src/daengs_cardgen ./src/daengs_cardgen

# 포트는 Cloud Run 이 PORT 로 준다. 모델은 CARDGEN_MODEL 로 고른다(cardgen.sh).
CMD ["sh", "-c", "exec /opt/venv/bin/uvicorn daengs_cardgen.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
```

```
# docker/cardgen/Dockerfile.dockerignore
# 빌드 컨텍스트는 저장소 루트다. 안 좁히면 frontend/node_modules 와 data/raw 까지 올라간다.
*
!backend/pyproject.toml
!backend/uv.lock
!backend/README.md
!backend/src/daengs_cardgen
backend/src/daengs_cardgen/**/__pycache__
```

`.gcloudignore` 의 `!docker/realtime/**` 줄 아래에 넣는다:

```
!docker/cardgen/
!docker/cardgen/**
```

- [ ] **Step 2: 배포 스크립트를 쓴다**

```bash
#!/usr/bin/env bash
# infra/gcp/cardgen.sh
# 도감 카드 생성 GPU 서비스 배포 (D-078). 모델마다 서비스 하나.
#   MODEL=klein PROJECT=daengs INVOKER=user:<gcloud 계정> bash infra/gcp/cardgen.sh
#   STEP=image|weights|deploy 로 한 단계만 (기본: 전부)
#
# ⚠ 돈이 나간다 — Cloud Build(CUDA 이미지 약 10GB), 가중치 받기 잡, 서비스가 떠 있는 L4 시간.
#   **돌리기 전에 사람에게 무엇을 몇 번 하는지 설명하고 승인받는다** (#544).
# ⚠ Git Bash 에서 돌린다. `MSYS_NO_PATHCONV=1` 을 켜지 마라 — gcloud 자체가 깨진다 (infra/gcp/README.md).
set -euo pipefail

: "${MODEL:?klein 또는 qwen}"
: "${INVOKER:?user:<gcloud 계정> — gcloud run services proxy 로 부를 사람}"
PROJECT="${PROJECT:-daengs}"
STEP="${STEP:-all}"
case "${MODEL}" in
  klein) MODEL_NAME=klein-4b ;;
  qwen)  MODEL_NAME=qwen-edit-2511 ;;
  *) echo "MODEL 은 klein 또는 qwen" >&2; exit 2 ;;
esac

REGION=asia-northeast3        # 이미지 저장소(daengs)는 서울 — 코퍼스·realtime 과 공유
GPU_REGION=asia-southeast1    # Cloud Run L4 가 있는 가장 가까운 리전 (서울엔 없다)
SERVICE="daengs-cardgen-${MODEL}"
BUCKET="daengs-cardgen-weights"
SA_EMAIL="corpus-pipeline@${PROJECT}.iam.gserviceaccount.com"
IMAGE_BASE="${REGION}-docker.pkg.dev/${PROJECT}/daengs/cardgen"
export CLOUDSDK_CORE_PROJECT="${PROJECT}"
export MSYS2_ARG_CONV_EXCL="--add-volume-mount"

# 태그는 이미지 입력의 내용 해시 (realtime.sh 와 같은 규칙). 목록이 곧 계약이다.
SHA="$(git ls-files -s backend/pyproject.toml backend/uv.lock backend/README.md \
        backend/src/daengs_cardgen docker/cardgen | git hash-object --stdin | cut -c1-7)"
IMAGE="${IMAGE_BASE}:${SHA}"

if [ "${STEP}" = all ] || [ "${STEP}" = image ]; then
  echo "== 이미지 (Cloud Build)"
  if gcloud artifacts docker images describe "${IMAGE}" >/dev/null 2>&1; then
    echo "(이미지 ${IMAGE} 이미 있음 — 빌드 생략)"
  else
    cfg="$(mktemp)"
    cat > "$cfg" <<CFG
steps:
  - name: gcr.io/cloud-builders/docker
    env: ['DOCKER_BUILDKIT=1']
    args: ['build', '-f', 'docker/cardgen/Dockerfile', '-t', '${IMAGE}', '.']
images: ['${IMAGE}']
options:
  machineType: 'E2_HIGHCPU_8'
  diskSizeGb: '100'
CFG
    gcloud builds submit --config="$cfg" .
    rm -f "$cfg"
  fi
fi

if [ "${STEP}" = all ] || [ "${STEP}" = weights ]; then
  echo "== 가중치 버킷 (${GPU_REGION})"
  gcloud storage buckets describe "gs://${BUCKET}" >/dev/null 2>&1 || \
    gcloud storage buckets create "gs://${BUCKET}" --location="${GPU_REGION}" --uniform-bucket-level-access
  gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
    --member="serviceAccount:${SA_EMAIL}" --role=roles/storage.objectAdmin >/dev/null
  echo "== 가중치 받기 잡 (CPU, 한 번)"
  gcloud run jobs deploy cardgen-weights --region="${GPU_REGION}" --image="${IMAGE}" \
    --service-account="${SA_EMAIL}" --cpu=4 --memory=16Gi --task-timeout=3h --max-retries=0 \
    --command=/opt/venv/bin/python --args=-m,daengs_cardgen.fetch,"${MODEL_NAME}" \
    --set-env-vars=HF_XET_CACHE=/tmp/xet \
    --add-volume=name=weights,type=cloud-storage,bucket="${BUCKET}" \
    --add-volume-mount=volume=weights,mount-path=/models
  gcloud run jobs execute cardgen-weights --region="${GPU_REGION}" --wait \
    --args=-m,daengs_cardgen.fetch,"${MODEL_NAME}"
fi

if [ "${STEP}" = all ] || [ "${STEP}" = deploy ]; then
  echo "== 서비스 배포 (${SERVICE})"
  # min 0 · max 1: 요청이 없으면 0대(0원). GPU 서비스는 인스턴스 기반 과금이라 떠 있는 동안은 유휴도 과금된다.
  # 포트는 곧바로 열리고 모델은 백그라운드로 올라간다 — 시작 프로브는 기본 TCP 로 충분하다(상한 240초).
  gcloud run deploy "${SERVICE}" --region="${GPU_REGION}" --image="${IMAGE}" \
    --service-account="${SA_EMAIL}" --no-allow-unauthenticated \
    --gpu=1 --gpu-type=nvidia-l4 --no-gpu-zonal-redundancy \
    --cpu=8 --memory=32Gi --no-cpu-throttling \
    --concurrency=1 --min-instances=0 --max-instances=1 --timeout=900 \
    --add-volume=name=weights,type=cloud-storage,bucket="${BUCKET}",readonly=true \
    --add-volume-mount=volume=weights,mount-path=/models \
    --set-env-vars="CARDGEN_MODEL=${MODEL_NAME},HF_HUB_OFFLINE=1,CARDGEN_QWEN_QUANT=${CARDGEN_QWEN_QUANT:-nf4}"
  gcloud run services add-iam-policy-binding "${SERVICE}" --region="${GPU_REGION}" \
    --member="${INVOKER}" --role=roles/run.invoker >/dev/null
  URL="$(gcloud run services describe "${SERVICE}" --region="${GPU_REGION}" --format='value(status.url)')"
  echo
  echo "완료: ${URL}"
  echo "개발 PC 에서 부르기 (PowerShell):"
  echo "  gcloud run services proxy ${SERVICE} --region=${GPU_REGION} --port=8091"
  echo "  curl.exe -s http://127.0.0.1:8091/health"
fi
```

```bash
#!/usr/bin/env bash
# infra/gcp/cardgen-teardown.sh
# 도감 카드 생성 GPU 서비스 삭제 (D-078). 11-17 크레딧 만료 전에 반드시 — 그 뒤는 자동 실비다.
#   PROJECT=daengs bash infra/gcp/cardgen-teardown.sh
set -euo pipefail

: "${PROJECT:?GCP 프로젝트 id}"
GPU_REGION=asia-southeast1
REGION=asia-northeast3
BUCKET="daengs-cardgen-weights"
IMAGE_BASE="${REGION}-docker.pkg.dev/${PROJECT}/daengs/cardgen"
export CLOUDSDK_CORE_PROJECT="${PROJECT}"

for s in daengs-cardgen-klein daengs-cardgen-qwen; do
  gcloud run services delete "$s" --region="${GPU_REGION}" --quiet || true
done
gcloud run jobs delete cardgen-weights --region="${GPU_REGION}" --quiet || true

echo "== 가중치 버킷 삭제 (수십 GB — 보관료가 계속 나간다)"
gcloud storage rm --recursive "gs://${BUCKET}" --quiet || true

echo "== cardgen 이미지 태그 삭제 (저장소 daengs 자체는 파이프라인과 공유라 안 지운다)"
IMAGES="$(gcloud artifacts docker images list "${IMAGE_BASE}" --format='value(IMAGE)' 2>/dev/null || true)"
if [ -n "${IMAGES}" ]; then
  while IFS= read -r img; do
    [ -n "$img" ] && { gcloud artifacts docker images delete "$img" --delete-tags --quiet || true; }
  done <<< "${IMAGES}"
fi
echo "끝. 서비스 계정·Artifact Registry 저장소는 코퍼스 파이프라인과 공유라 남긴다."
```

- [ ] **Step 3: gcloud 인자가 이 설치본에서 맞는지 확인한다 (돈 안 나감)**

Run (PowerShell):
```powershell
gcloud run deploy --help | Select-String -Pattern "no-gpu-zonal-redundancy|add-volume|no-cpu-throttling"
gcloud run services proxy --help | Select-Object -First 5
gcloud meta list-files-for-upload | Select-String -Pattern "cardgen|\.env"
```
Expected: 세 플래그가 도움말에 있고, `services proxy` 도움말이 나오고, 업로드 목록에 `docker/cardgen/…`·`backend/src/daengs_cardgen/…` 가 있고 `.env` 는 **없다**. 플래그가 없으면 `gcloud components update` 를 사람에게 요청하고 멈춘다.

- [ ] **Step 4: README 에 절을 더한다**

`infra/gcp/README.md` 의 `## 자주 걸리는 것` 바로 위에 넣는다:

```markdown
## `cardgen.sh` — 도감 카드 생성 GPU 서비스 (D-078, #544)

모델마다 Cloud Run **서비스** 하나(`daengs-cardgen-klein` · `daengs-cardgen-qwen`), 싱가포르 L4,
`min 0 · max 1`. 코드는 `docker/cardgen/`, 가중치는 버킷 `daengs-cardgen-weights` 를 `/models` 로 마운트.

- **돈이 나간다.** Cloud Build(CUDA 이미지), 가중치 받기 잡, 떠 있는 L4 시간. 요청 뒤에도 인스턴스가
  내려가기 전까지 과금된다(인스턴스 기반 과금 필수). 돌리기 전에 사람 승인.
- **부르는 법** — 개발 PC 의 `gcloud auth print-identity-token` 은 이 서비스에서 미인증으로 취급된다
  (realtime 절 3번). 대신 `gcloud run services proxy daengs-cardgen-klein --region=asia-southeast1 --port=8091`
  을 켜 두고 `http://127.0.0.1:8091` 을 부른다. `INVOKER` 로 준 계정에 `run.invoker` 가 걸려 있어야 한다.
- **VM backend 에 연결하지 않는다** — `DAENGS_CARDGEN_URL` 을 VM 에 넣으면 앱 경로가 GPU 서비스를 쓰는데,
  앱 경로 정리 기준이 콜드 스타트를 모른다(#544 남은 것).
- **지울 때** — `PROJECT=daengs bash infra/gcp/cardgen-teardown.sh`. 서비스 둘·잡·가중치 버킷·이미지 태그.
```

- [ ] **Step 5: 셸 문법만 본다**

Run (Git Bash): `bash -n infra/gcp/cardgen.sh && bash -n infra/gcp/cardgen-teardown.sh && echo ok`
Expected: `ok`

- [ ] **Step 6: 커밋 (컨트롤러)**

```powershell
git add docker/cardgen .gcloudignore infra/gcp/cardgen.sh infra/gcp/cardgen-teardown.sh infra/gcp/README.md
git commit -m "카드 생성 GPU 서비스의 CUDA 이미지와 배포·삭제 스크립트를 둔다`n`nCo-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: 🔴 승인 게이트 — 이미지 · klein 가중치 · klein 배포 · 첫 호출 (컨트롤러가 사람과)

**돈이 나가는 첫 Task 다. 하위 에이전트에게 맡기지 않는다.**

**Files:**
- Modify: `docs/cardimage/worklog.md` (실측 기록)

- [ ] **Step 1: 사람에게 설명하고 승인받는다**

설명할 것: ① Cloud Build 로 CUDA 이미지 한 번(약 10GB, 20~40분 — 코퍼스 CUDA 이미지 실측 기준 추정) ② klein 가중치 받기 잡 한 번(CPU, 약 16GB) ③ `daengs-cardgen-klein` 배포 ④ `/health` 와 `/generate` 한 장(4월 틀 + `_03` 사진, seed 1). GPU 비용은 떠 있는 시간 × L4(코퍼스 잡 실측 11분 ₩250 → 시간당 약 ₩1,400) — 첫 호출·유휴 포함 30분 안쪽 예상. 승인 전에는 아래를 실행하지 않는다.

- [ ] **Step 2: 이미지와 가중치**

Run (Git Bash): `MODEL=klein INVOKER=user:<계정> STEP=image bash infra/gcp/cardgen.sh` 다음 `STEP=weights`
Expected: 빌드 로그에 `torch 2.13.0+cu126 cuda 12.6`, 잡 로그에 `fetched klein-4b -> /models/hub/...`

- [ ] **Step 3: 배포와 콜드 스타트 실측**

Run: `MODEL=klein INVOKER=user:<계정> STEP=deploy bash infra/gcp/cardgen.sh`
그다음 PowerShell 창 하나에서 `gcloud run services proxy daengs-cardgen-klein --region=asia-southeast1 --port=8091`, 다른 창에서:
```powershell
Measure-Command { curl.exe -s http://127.0.0.1:8091/health } | Select-Object TotalSeconds
curl.exe -s http://127.0.0.1:8091/health
```
Expected: 첫 `/health` 는 인스턴스가 뜨는 만큼만 걸리고 곧 `{"model":null,"ready":false,"load_seconds":null,"error":null}` 로 돌아온다(포트는 곧바로 열리고 모델은 백그라운드로 올라간다). `ready:true` 가 될 때까지 `/health` 를 몇 초 간격으로 다시 불러 `{"model":"klein-4b","ready":true,"load_seconds":<초>,"error":null}` 을 받고, 첫 `TotalSeconds` 와 `load_seconds` 를 적는다. **`error` 가 채워지면** 로그를 읽고 멈춘다(아래와 같이). **실패하면** `gcloud run services logs read daengs-cardgen-klein --region=asia-southeast1 --limit=100` 을 보고 원인을 적은 뒤 사람에게 보고한다(추측으로 고쳐 재배포하지 않는다).

- [ ] **Step 4: 한 장 생성 (사람 승인 범위 안에서)**

Run (in `backend/`):
```powershell
uv run python tools/cardgen_compare.py --engine cardgen --url http://127.0.0.1:8091 --photos ../cardimage/test/_03.jpg --months 4 --seeds 1 --out ../cardimage/out/_cardgen/smoke-klein
```
Expected: `results.jsonl` 한 줄(`likeness`·`drift`·`plate_shift`·`service.seconds`)과 PNG 한 장. 사람에게 PNG 를 보여 준다.

- [ ] **Step 5: 떠 있던 시간을 확인한다**

Run: `gcloud run services describe daengs-cardgen-klein --region=asia-southeast1 --format="value(status.url)"` 로 살아 있는지 보고, 15분 이상 호출하지 않은 뒤 Cloud Console 의 인스턴스 수가 0 이 되는 시각을 적는다(「요청 뒤 과금 꼬리」 실측).

- [ ] **Step 6: worklog 에 적고 커밋**

`docs/cardimage/worklog.md` 맨 위 절(09-15)에 이미지 빌드 시간·가중치 크기·콜드 스타트(`TotalSeconds`·`load_seconds`)·한 장 시간·유휴 뒤 내려간 시각·결과 PNG 경로를 **잰 값만** 적는다.

```powershell
git add docs/cardimage/worklog.md
git commit -m "FLUX.2-klein-4B 서비스를 처음 띄워 콜드 스타트와 한 장 시간을 잰다`n`nCo-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: 🔴 게이트 — Qwen 2511 이 L4 에 올라가는가 (컨트롤러가 사람과)

**Files:**
- Modify: `docs/cardimage/worklog.md`

- [ ] **Step 1: 사람에게 설명하고 승인받는다**

설명할 것: Qwen 가중치 약 57GB 받기 잡 한 번, `daengs-cardgen-qwen`(nf4) 배포, `/health` 와 한 장. 가중치 로드가 버킷에서 수십 GB 라 콜드 스타트가 klein 보다 훨씬 길 수 있다(실측 전).

- [ ] **Step 2: 가중치 · 배포**

Run: `MODEL=qwen INVOKER=user:<계정> STEP=weights bash infra/gcp/cardgen.sh` 다음 `STEP=deploy`
(이미지는 Task 6 과 같은 태그라 빌드가 생략된다)

- [ ] **Step 3: 판정**

proxy 를 8092 로 켜고 Task 6 Step 3·4 와 같은 방법으로 `/health` 와 한 장(`--out ../cardimage/out/_cardgen/smoke-qwen`).

| 결과 | 다음 |
| --- | --- |
| 뜨고 한 장이 멀쩡하다 | Task 8 |
| 로그에 `CUDA out of memory` · 컨테이너 메모리 초과(32GiB) · `/health` 의 `error` 가 채워짐 | **멈춘다.** 로그 발췌와 함께 사람에게 선택지를 올린다: ① RTX PRO 6000(96GB)로 `CARDGEN_QWEN_QUANT=none` 재배포(최소 20 CPU·80GiB, 비용 큼) ② Qwen 을 이번 비교에서 뺀다 |
| 뜨지만 결과가 깨진다(노이즈·검은 화면) | **멈춘다.** PNG 를 보여 주고 같은 선택지를 올린다 |

- [ ] **Step 4: worklog 에 적고 커밋**

잰 값(가중치 받기 시간·콜드 스타트·한 장 시간·GPU 메모리 로그가 있으면 그 줄)과 판정을 적는다.

```powershell
git add docs/cardimage/worklog.md
git commit -m "Qwen-Image-Edit-2511 nf4 가 L4 서비스에 올라가는지 판정을 적는다`n`nCo-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: 🔴 승인 — 같은 조건 비교 실행과 결과 표 (컨트롤러가 사람과)

**Files:**
- Create: `docs/cardimage/compare-2026-09-15-cardgen.md`
- Modify: `docs/cardimage/worklog.md`

- [ ] **Step 1: 비교 행렬을 사람과 정하고 승인받는다**

제안: 사진 3장(`_03` + 사람이 고른 2장) × 달 2(4·9) × seed 2 = **엔진당 12장**, 엔진 셋(klein · Qwen · gemini). gemini 12장 약 $1.2 + 검수 36회, GPU 는 klein 수 분 · Qwen 은 Task 7 의 한 장 시간 × 12 + 콜드 스타트. 사람이 사진·장수를 바꾸면 그대로 따른다.

- [ ] **Step 2: 세 엔진을 돌린다**

Run (in `backend/`, proxy 두 개를 켠 채):
```powershell
$P = "../cardimage/test/<사진1>.jpg,../cardimage/test/<사진2>.jpg,../cardimage/test/<사진3>.jpg"
uv run python tools/cardgen_compare.py --engine cardgen --url http://127.0.0.1:8091 --photos $P --months 4,9 --seeds 1,2 --out ../cardimage/out/_cardgen/klein
uv run python tools/cardgen_compare.py --engine cardgen --url http://127.0.0.1:8092 --photos $P --months 4,9 --seeds 1,2 --out ../cardimage/out/_cardgen/qwen
uv run python tools/cardgen_compare.py --engine gemini --photos $P --months 4,9 --seeds 1,2 --out ../cardimage/out/_cardgen/gemini
```
(`<사진N>` 은 Step 1 에서 사람이 고른 파일 이름으로 바꾼다.)
Expected: 각 `results.jsonl` 12줄.

- [ ] **Step 3: 결과 표를 쓴다**

`docs/cardimage/compare-2026-09-15-cardgen.md` 에 엔진별로 — 닮음 평균·분포(1~5), `text_ok`·`avatar_ok` 비율, `drift` 의 `|dx|+|dy|` 최대·평균과 `mad_at_zero`, `plate_shift` 범위, 한 장 시간(서비스 쪽 `service.seconds` / 전체 `seconds`), 콜드 스타트(Task 6·7 실측) — 을 **results.jsonl 에서 다시 센 값**으로 적는다. 사람이 PNG 를 보고 한 판정은 따로 한 칸. 결론 한 문장은 사람의 판정을 받은 뒤 적는다.

- [ ] **Step 4: 커밋**

```powershell
git add docs/cardimage/compare-2026-09-15-cardgen.md docs/cardimage/worklog.md
git commit -m "FLUX.2-klein-4B · Qwen 2511 · Nano Banana 2 를 같은 조건으로 비교한 결과를 적는다`n`nCo-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: 문서 마무리 — D-078 · README · 삭제 목록 · PR 본문

**Files:**
- Modify: `docs/decisions.md` (D-078)
- Modify: `docs/cardimage/README.md` (「지금 상태」·「정해진 것」·「파일 위치」)
- Modify: `docs/cardimage/worklog.md`
- Modify: `CLAUDE.md` (폴더 표에 `backend/src/daengs_cardgen/` 한 줄)

- [ ] **Step 1: D-078 을 쓴다**

`docs/decisions.md` 끝에 D-076 과 같은 형식으로 — 제목 `### 도감 카드 생성의 오픈 모델 경로는 별도 GPU 서비스(daengs_cardgen, Cloud Run L4)로`, 날짜·#544, **경계**(패키지·그룹을 나눈 이유: 컨테이너가 둘인 것은 GPU 때문이고 패키지는 torch 가 backend 로 새지 않게), **실행 자리**(service 를 고른 이유와 인스턴스 기반 과금·min 0), **diffusers**(ComfyUI 대신인 이유와 대가), **갈림길**(`DAENGS_CARDGEN_URL`, 비면 D-074 그대로), **후보 제외 근거**(JoyAI 사용자 제외 · HunyuanImage 한국 제외 라이선스 · klein 9B·dev 비상업), **되돌리기**(URL 비우기 + teardown), Task 8 결과 문서 링크.

- [ ] **Step 2: README · CLAUDE.md**

`docs/cardimage/README.md` 「지금 상태」 맨 위에 이 카드의 한 문단(무엇이 떠 있고 무엇을 쟀는지, 결과 문서 링크, 11-17 전에 teardown), 「파일 위치」 표에 `backend/src/daengs_cardgen/` · `docker/cardgen/` · `infra/gcp/cardgen*.sh` · `backend/tools/cardgen_compare.py` · `cardimage/out/_cardgen/`(미추적) 행, 「정해진 것」에 후보 둘·실행 자리·diffusers 행. `CLAUDE.md` 폴더 표의 `backend/src/daengs_cardimage/` 행 아래에:

```markdown
| `backend/src/daengs_cardgen/` | 도감 카드 생성 **GPU 서비스**(diffusers — Qwen-Image-Edit-2511 · FLUX.2-klein-4B). backend 가 import 하지 않고 HTTP 로만 부른다(`DAENGS_CARDGEN_URL`, 비면 Nano Banana 2 그대로). 전용 그룹 `cardgen` + `docker/cardgen/` CUDA 이미지로 Cloud Run L4(asia-southeast1)에서만 돈다 — D-078. 11-17 전에 `infra/gcp/cardgen-teardown.sh` |
```

- [ ] **Step 3: 전체 검사 (컨트롤러)**

Run (in `backend/`): `uv run check` 다음 `uv run pytest`
Expected: 둘 다 통과. 실패하면 출력 그대로 보고하고 고친 뒤 다시.

- [ ] **Step 4: 커밋 · PR 본문 갱신**

```powershell
git add docs/decisions.md docs/cardimage/README.md docs/cardimage/worklog.md CLAUDE.md
git commit -m "D-078 과 카드 생성 GPU 서비스 인수인계 문서를 적는다`n`nCo-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
git push
```
PR #544 본문의 「작업 목록」 체크, 「확인한 것」, 「남은 것」(아래)을 `gh pr edit 544 --body-file <파일>` 로 갱신한다(일곱 제목 유지).

「남은 것」에 반드시 넣을 것:
- 앱 경로(`/app/ai-cards`)의 정리 기준이 `DAENGS_CARDGEN_URL` 을 쓸 때 콜드 스타트를 반영하도록 — VM 에 연결하기 전 선행 카드
- 11-17 전에 `infra/gcp/cardgen-teardown.sh` (서비스·잡·가중치 버킷·이미지)
- 고른 모델의 프롬프트 다듬기(필요하면 테스트 동안 min 1 로 켜 두고 반복)
