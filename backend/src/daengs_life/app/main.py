"""FastAPI 앱 (RAG-027).

여기서 하는 일은 셋뿐이다 — **앱을 만들고, 컨트롤러를 등록하고, 수명을 관리한다.**
엔드포인트를 여기 직접 붙이지 않는다: 파트①의 `/ask` 가 들어올 때 이 파일에서 겹치는 것이
**등록 한 줄**이어야 두 브랜치가 안 부딪힌다 (RAG-027 마지막 절).

**이 앱은 `daengs_backend` 와 별개의 ASGI 앱이다** (D-018). 아직 그쪽에 등록하지 않았다 —
아래 lifespan 이 임베딩 모델을 상주시켜서, 배포되는 API 프로세스에 그대로 붙이면 그 프로세스가
모델 로드분을 같이 문다. 띄우려면 이 모듈을 직접 가리킨다:

    uv run uvicorn daengs_life.app.main:app --port 8100

옛 `backend/main.py` 2줄 shim 은 이관하며 없앴다 — `feat/rag` 와 `feat/realtime` 이 병렬로
살아 있는 동안 rename-vs-modify 충돌을 막던 것이고, 그 조건이 끝났다.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from daengs_life.app.controllers import ask, walk
from daengs_life.app.deps import get_cache, release_encoder, warm_up_encoder


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Redis 연결을 **앱이 뜰 때 한 번** 만든다 (④-c).

    첫 요청에 미루면 그 요청만 커넥션 수립까지 물고 있게 되는데, ⑤-b 의 8초 예산이 그
    시간까지 세게 된다. 여기서 미리 만들면 예산은 실제 API 호출에만 쓰인다.

    **연결이 안 돼도 앱은 뜬다** — `Cache()` 가 스스로 메모리로 떨어진다. 캐시는 가속이지
    정답의 원천이 아니다.
    """
    get_cache()

    # 임베딩 모델도 **앱이 뜰 때 한 벌** 올린다 (RAG-028 ①). 호출마다 올렸다 내리면 요청당 5~7초다.
    # **실패해도 앱은 뜬다** — `ml` 그룹(torch)이 없는 환경에서도 `/walk` 는 돌아야 하고,
    # 캐시가 Redis 없이 뜨는 것과 같은 태도다. 그 경우 `/ask` 만 503 이 된다.
    #
    # **여기서는 동기로 부른다.** 이 앱은 `/ask` 전용에 가까워서 모델이 올라오기 전에 뜰 이유가
    # 없다. 로그인·`/walk` 와 한 프로세스인 `daengs_backend` 쪽은 그럴 이유가 있어서
    # 백그라운드로 돌린다 (D-021) — 삼키는 방식과 로그는 `warm_up_encoder` 가 공유한다.
    warm_up_encoder()

    yield
    get_cache.cache_clear()
    release_encoder()


def create_app() -> FastAPI:
    app = FastAPI(
        title="강아지 AI 생활 비서",
        summary="제도·문서형 RAG + 실시간 산책 적합도",
        lifespan=lifespan,
    )

    # 배포 시점에 좁힌다. 지금은 로컬 Next.js 확인용이라 열어 둔다 (기존 main.py 와 같은 설정)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- 컨트롤러 등록. 새 엔드포인트는 여기 한 줄만 는다 ---
    app.include_router(walk.router)
    app.include_router(ask.router)

    @app.get("/", tags=["test"])
    def read_root() -> dict[str, str]:
        return {"Daengs": "Life Assistant"}

    return app


app = create_app()

__all__ = ["app", "create_app"]
