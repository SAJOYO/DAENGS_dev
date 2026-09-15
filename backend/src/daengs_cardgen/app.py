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
