"""앱 조립. 라우터 등록과 미들웨어까지만 하고, 로직은 두지 않습니다."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from daengs_backend.config import settings
from daengs_backend.core.database import engine
from daengs_backend.routers import app_auth, auth, health, training

# 리로드 감시 대상. 폴링으로 도는 환경(컨테이너 + 바인드 마운트)에서
# 범위를 좁혀 두지 않으면 CPU 를 계속 씁니다.
SRC_DIR = Path(__file__).resolve().parents[1]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield
    # 커넥션 풀을 정리합니다. 리로드가 잦은 개발 모드(D-006)에서
    # 이게 없으면 죽은 워커가 잡고 있던 연결이 남습니다.
    await engine.dispose()


app = FastAPI(title="DAENGS API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router)
# 앱 회원(카카오)용. 관리자와 경로가 겹치지 않게 /auth/app/* 입니다.
app.include_router(app_auth.router)
app.include_router(training.router)


def dev() -> None:
    """`uv run dev` — 개발 서버. `fastapi dev` 와 같은 설정입니다."""
    import uvicorn

    uvicorn.run(
        "daengs_backend.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
        reload_dirs=[str(SRC_DIR)],
    )


def run() -> None:
    """`uv run run` — 운영 서버. `fastapi run` 과 같은 설정입니다."""
    import uvicorn

    uvicorn.run(
        "daengs_backend.main:app",
        host="0.0.0.0",
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    dev()
