from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from daengs_backend.config import settings

# 리로드 감시 대상. 폴링으로 도는 환경(컨테이너 + 바인드 마운트)에서
# 범위를 좁혀 두지 않으면 CPU 를 계속 씁니다.
SRC_DIR = Path(__file__).resolve().parents[1]

app = FastAPI(title="DAENGS API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


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
