from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from daengs_backend.config import settings

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
        host="127.0.0.1",
        port=8000,
        reload=True,
    )


def run() -> None:
    """`uv run run` — 운영 서버. `fastapi run` 과 같은 설정입니다."""
    import uvicorn

    uvicorn.run(
        "daengs_backend.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )


if __name__ == "__main__":
    dev()
