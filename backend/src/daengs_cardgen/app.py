"""GPU 서비스 HTTP 앱. 모델은 기동 때 한 번 올리고, 요청은 한 번에 하나씩 처리한다.

**포트는 곧바로 열린다** — 모델은 lifespan 이 띄운 백그라운드 스레드가 올린다. Cloud Run 의 시작
프로브는 240초가 상한인데 FLUX.2-klein-4B 도 GCS FUSE 에서 425~430초 걸린다(09-15 실측).
올리는 동안 들어온 `/generate` 는 `ready_timeout_s`(Cloud Run 요청 타임아웃 900초보다 짧게)까지
기다렸다가 처리한다 — 그래서 콜드 스타트 요청은 여전히 503 이 아니라 **느리게 성공**한다.
올린 시간은 `/health` 의 `load_seconds`, 올리다 실패하면 `error` 에 남고 `/generate` 는 503 `load_failed`.
"""

from __future__ import annotations

import base64
import binascii
import io
import os
import threading
import time
import traceback
from collections.abc import AsyncIterator, Callable
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


def _default_loader() -> CardGenModel:
    from daengs_cardgen.diffusion import model_by_name

    loaded = model_by_name(os.environ["CARDGEN_MODEL"])
    loaded.load()
    return loaded


def create_app(
    model: CardGenModel | None = None,
    *,
    loader: Callable[[], CardGenModel] | None = None,
    ready_timeout_s: float = 840.0,
) -> FastAPI:
    state: dict[str, Any] = {"model": model, "load_seconds": None, "load_error": None}
    ready = threading.Event()
    lock = threading.Lock()
    if model is not None:
        ready.set()

    def _load() -> None:
        started = time.monotonic()
        try:
            loaded = (loader or _default_loader)()
        except Exception as exc:  # noqa: BLE001 — 어떤 실패든 /health 와 /generate 로 드러낸다
            state["load_error"] = f"{type(exc).__name__}: {exc}"
            traceback.print_exc()
        else:
            state["model"] = loaded
            state["load_seconds"] = round(time.monotonic() - started, 1)
            print(f"cardgen model={loaded.name} load_seconds={state['load_seconds']}", flush=True)
        finally:
            ready.set()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if not ready.is_set():
            threading.Thread(target=_load, name="cardgen-load", daemon=True).start()
        yield

    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, Any]:
        current = state["model"]
        return {
            "model": getattr(current, "name", None),
            "ready": current is not None,
            "load_seconds": state["load_seconds"],
            "error": state["load_error"],
        }

    @app.post("/generate")
    def generate(body: GenerateBody) -> Response:
        try:
            images = [_decode(item) for item in body.images_b64]
        except ValueError as exc:
            return JSONResponse({"code": "bad_image", "message": f"이미지를 읽을 수 없습니다: {exc}"}, status_code=400)
        if not ready.wait(ready_timeout_s):
            return JSONResponse({"code": "not_ready", "message": "모델을 올리는 중입니다"}, status_code=503)
        current = state["model"]
        if current is None:
            return JSONResponse({"code": "load_failed", "message": state["load_error"]}, status_code=503)
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
