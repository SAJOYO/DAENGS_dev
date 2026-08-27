"""FastAPI 앱 (RAG-027).

여기서 하는 일은 셋뿐이다 — **앱을 만들고, 컨트롤러를 등록하고, 수명을 관리한다.**
엔드포인트를 여기 직접 붙이지 않는다: 파트①의 `/ask` 가 들어올 때 이 파일에서 겹치는 것이
**등록 한 줄**이어야 두 브랜치가 안 부딪힌다 (RAG-027 마지막 절).

`backend/main.py` 는 이 모듈을 가리키는 2줄 shim 으로 남아 있다. 완전 이동은 `feat/rag` 가
합쳐진 뒤에 한다 — 지금 옮기면 rename-vs-modify 충돌이 난다.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from daengs_life.app.controllers import ask, walk
from daengs_life.app.deps import get_cache, get_encoder


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
    try:
        get_encoder()
    except Exception as e:                       # noqa: BLE001 — 무엇이든 앱을 못 세우면 안 된다
        print(f"[lifespan] 임베딩 모델을 못 올렸다 — /ask 는 503 이 된다: {type(e).__name__}: {e}")

    yield
    get_cache.cache_clear()
    get_encoder.cache_clear()


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
