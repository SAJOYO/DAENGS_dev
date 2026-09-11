"""realtime 전용 ASGI 앱 — Cloud Run 서비스가 띄우는 것 (D-068).

**`app/main.py` 와 왜 갈랐나.** 저쪽은 `/life/ask` 까지 등록한다. 이 이미지에는 `ml`
그룹(torch)이 없어서 그 import 가 깨질 수 있고, 안 깨져도 **realtime 서비스가 RAG 코드를
들고 있는 것**은 분리의 취지에 안 맞는다. 저쪽 파일은 손대지 않았다 — 개발 PC 에서
`uvicorn daengs_life.app.main:app` 으로 셋 다 띄우는 길은 그대로다.

**예열이 없다.** 여기에는 임베딩 모델이 없으므로 올릴 것도 놓을 것도 없다. `get_cache()` 만
미리 연다 — 첫 요청에 미루면 Redis 커넥션 수립 시간이 그 요청의 8초 예산(⑤-b)에 얹힌다.

**인증은 여기 없다.** Cloud Run 의 IAM 이 문이고, 로그인 검사는 `daengs_backend` 가 한다
(D-068 §5 ⓐ). 그래서 이 앱은 `daengs_backend` 를 여전히 **모른다** — 저쪽을 import 하면
의존 방향이 뒤집혀 이 앱과 `python -m daengs_life.realtime walk` CLI 가 그 저장소의 인증
없이는 못 도는 물건이 된다 (D-018 · RAG-001 원칙 1).

⚠ **엔드포인트를 여기 직접 붙이지 않는다.** `app/main.py` 와 같은 규칙이다 (RAG-027) —
여기서 하는 일은 앱을 만들고 컨트롤러를 등록하고 수명을 관리하는 것 셋뿐이다. 예외는 아래
`/health` 하나이고, 그것은 도메인이 아니라 Cloud Run 이 컨테이너를 보는 창이다. **`/healthz`
가 아니라 `/health`인 이유는 그 함수의 docstring 을 보라** — 구글 프런트엔드가 `/healthz`
를 가로채 컨테이너까지 안 보낸다 (2026-09-11 실측).
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from daengs_life.app.controllers import walk, weather
from daengs_life.app.deps import get_cache


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    """Redis 연결을 앱이 뜰 때 한 번 만든다 (RT-001 ④-c).

    **연결이 안 돼도 앱은 뜬다** — `Cache()` 가 스스로 프로세스 메모리로 떨어진다. 캐시는
    가속이지 정답의 원천이 아니다.

    ⚠ 다만 `get_cache` 가 `lru_cache` 라 **그 판단이 프로세스 생애에 한 번뿐**이다. Redis 가
    늦게 뜨는 환경에서는 그 인스턴스가 끝까지 메모리 캐시로 돈다 — 그러면 일 예산 카운터가
    이 인스턴스에서만 세어져 D-068 §4 가 기각한 상태가 조용히 재현된다. Cloud Run 로그의
    캐시 저하 경고를 그래서 봐야 한다.
    """
    get_cache()
    yield
    get_cache.cache_clear()


def create_app() -> FastAPI:
    app = FastAPI(
        title="DAENGS 실시간 산책·날씨",
        summary="산책 적합도 + 과거 관측. 상태는 Redis 에 있고 이 프로세스에는 없다",
        lifespan=lifespan,
    )

    # **CORS 를 열지 않는다.** 브라우저가 이 서비스를 직접 부르는 일이 없다 — 부르는 것은
    # `daengs_backend` 하나이고 그것은 서버 대 서버다 (D-068 §5 ⓐ). 여기를 `["*"]` 로 열면
    # 「인증은 게이트웨이가 한다」와 어긋나는 신호가 코드에 남는다.

    # --- 컨트롤러 등록. 새 엔드포인트는 여기 한 줄만 는다 ---
    app.include_router(walk.router)
    app.include_router(weather.router)

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        """Cloud Run 이 컨테이너를 살아 있다고 볼 자리.

        **`/healthz` 가 아니라 `/health` 다 — 되돌리지 말 것.** Cloud Run 앞의 구글
        프런트엔드가 정확히 `/healthz` 경로 하나를 가로챈다. 요청이 이 컨테이너에
        아예 안 닿고 구글의 일반 404 HTML 이 돌아오며, 컨테이너 로그에는 요청 기록조차
        안 남는다. 2026-09-11 실측(같은 서비스·같은 배포): `/healthz` 는 구글 404, 반면
        `/health` · `/_healthz` · `/healthzz` · `/livez` · `/readyz` · `/openapi.json` 은
        전부 이 앱까지 와서 앱이 직접 답했다 — 그래서 예약된 것은 정확히 `/healthz`
        하나라고 안다. **표에 없는 다른 경로가 안전하다고는 단정하지 않는다** — 잰 것만
        적는다. 증상이 고약한 이유: 리비전은 Ready 인데 서비스는 죽은 것처럼 보이고,
        컨테이너 로그에 아무 단서도 안 남는다.

        **외부 API 도 Redis 도 부르지 않는다.** 여기서 그것들을 확인하면 기상청이 죽은 날
        컨테이너가 죽은 것으로 취급돼 재기동이 돌고, 그러면 `stale` 로 답할 수 있었던
        요청까지 못 받는다 (⑤-c 가 만든 저하 경로를 헬스체크가 무효화한다).
        """
        return {"status": "ok"}

    return app


app = create_app()

__all__ = ["app", "create_app"]
