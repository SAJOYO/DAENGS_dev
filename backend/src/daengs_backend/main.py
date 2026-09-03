"""앱 조립. 라우터 등록과 미들웨어까지만 하고, 로직은 두지 않습니다."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from daengs_backend.config import settings
from daengs_backend.core.database import engine
from daengs_backend.core.deps import Perm, admin_or_app_user
from daengs_backend.routers import (
    app_auth,
    assistant,
    auth,
    crawl,
    gait,
    health,
    pet,
    training,
    walk_spatial_diary,
)

# ⚠️ 별칭입니다. 아래에서 `daengs_life` 의 `walk`(산책 **적합도**)를 같은 이름으로
# import 하는데, 그쪽이 나중에 와서 이걸 가려 버립니다 — 그러면 include_router 가
# 조용히 엉뚱한 라우터를 두 번 등록합니다. 기록은 `/app/walks`, 적합도는 `/walk` 입니다.
from daengs_backend.routers import walk as app_walks
from daengs_backend.services.training_rag import release_training_runtime

# 이 앱이 `daengs_life` 를 부르는 **유일한 자리**입니다. D-018 이 일부러 안 그은 선을
# 여기서만 긋습니다 — 접점은 **등록 두 줄과 예열 한 줄**이 전부입니다.
#
# `/ask` 는 임베딩 모델을 씁니다. 그래도 여기 붙이는 것이 D-021 의 결정입니다 — 모델을
# 배포되는 API 프로세스에 그대로 상주시키고(약 2.4GB), 2단계에서 조건이 오면
# `daengs_life.app.main:app`(이미 독립 ASGI 앱)을 따로 띄우고 이 자리를 게이트웨이로 바꿉니다.
# 이사가 싼 채로 남으려면 **접점이 이 세 줄을 넘으면 안 됩니다.**
#
# 그래도 **import 는 여전히 가벼워야 합니다.** `daengs_life` 쪽이 torch·psycopg 를 전부
# 함수 안에서 부르므로 모듈을 읽는 것만으로는 아무것도 안 올라옵니다 — 그 사실을
# `tests/test_main_stays_light.py` 가 기계로 지킵니다. 무거워지는 것은 import 가 아니라
# 아래 lifespan 의 예열이고, 그래서 그것만 백그라운드로 돌립니다.
from daengs_life.app.controllers import ask, walk
from daengs_life.app.deps import get_cache, release_encoder, warm_up_encoder

# ⚠️ 스크리닝도 같은 규칙입니다 — 이 import 로 torch 가 딸려 오면 안 됩니다.
#    `service.py` 최상단은 fastapi 와 `agent`(config 만 씀)뿐이고, 가중치는
#    첫 요청 때 올라옵니다 (D-039).
from daengs_screening.service import router as screening_router

# 리로드 감시 대상. 폴링으로 도는 환경(컨테이너 + 바인드 마운트)에서
# 범위를 좁혀 두지 않으면 CPU 를 계속 씁니다.
SRC_DIR = Path(__file__).resolve().parents[1]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # 실시간 캐시를 미리 엽니다 (RT-001 ④-c). `get_cache` 는 lru_cache 라 여기서
    # `Cache()` 가 만들어지고 그때 Redis 연결을 시도합니다. 첫 요청에 미루면 그 비용이
    # 요청 하나에 통째로 붙습니다.
    #
    # **연결이 안 돼도 앱은 떠야 합니다** — `open_store()` 가 실패를 예외가 아니라
    # 저하로 다뤄 프로세스 메모리로 떨어집니다. 다만 lru_cache 라 그 판단이 프로세스
    # 생애에 한 번뿐이라, compose 에서 backend 가 redis 의 healthcheck 를 기다립니다.
    get_cache()

    # 임베딩 모델은 **백그라운드로** 올립니다 (D-021).
    #
    # 여기서 동기로 부르면 안 됩니다 — 이 프로세스에는 로그인·`/walk`·`/training` 이 같이
    # 살고, 가중치를 RAM 으로 올리는 5~7초 동안 **API 전체가 502** 입니다. 컨테이너가
    # `reload=True` 로 돌고 배포가 마운트된 소스를 갈아 끼우므로 그 일이 backend 코드가
    # 바뀌는 배포마다 일어납니다.
    #
    # 예열이 도는 동안 들어온 `/ask` 는 **기다리지 않고 즉시 503 + `Retry-After`** 입니다
    # (#37). 기다리게 하면 콜드 캐시에서 nginx 의 60초를 넘겨 사용자가 HTML 504 를 받습니다.
    # 그 판단은 여기가 아니라 `deps.get_encoder` 가 합니다 — 두 벌을 막는 `deps._ENCODER_LOCK`
    # 은 그대로 있고, 바뀐 것은 그 락을 **기다리는 방식**뿐입니다.
    #
    # `settings.warm_up_encoder` 로 끌 수 있습니다 — 테스트와 개발 PC 용입니다. 끄면 모델이
    # 안 뜨는 게 아니라 **첫 `/ask` 가 로드를 뭅니다.** 그때는 아무도 예열하고 있지 않으므로
    # 위의 503 이 아니라 기다리는 쪽이 맞습니다 (`deps._WARM_UP_IN_PROGRESS`).
    warm_up = (asyncio.create_task(asyncio.to_thread(warm_up_encoder))
               if settings.warm_up_encoder else None)

    yield

    # 예열이 아직 도는 중이면 기다리지 않습니다. 스레드는 자기 일을 마치고 끝나지만,
    # 종료를 그 5~7초만큼 붙잡을 이유가 없습니다.
    if warm_up is not None:
        warm_up.cancel()
        with suppress(asyncio.CancelledError):
            await warm_up
    # 상주 모델을 놓습니다. 리로드가 잦은 개발 모드에서 이게 없으면 죽은 워커의 1.2GB 가
    # 새 워커의 것과 함께 남습니다 — `engine.dispose()` 와 같은 이유이고, 여기서는 단위가 GB 입니다.
    release_encoder()
    release_training_runtime()
    # 커넥션 풀을 정리합니다. 리로드가 잦은 개발 모드(D-006)에서
    # 이게 없으면 죽은 워커가 잡고 있던 연결이 남습니다.
    await engine.dispose()
    # 같은 이유입니다 — lru_cache 가 `Cache` 를, 그게 Redis 커넥션 풀을 잡고 있습니다.
    get_cache.cache_clear()


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
# 강아지 프로필. 라우터 자체가 CurrentAppUser 로 잠겨 있습니다.
app.include_router(pet.router)
# 보행 분석 orchestration (D-043). 라우터가 CurrentAppUser 로 잠겨 있고, 분석 자체는
# 별도 워커(daengs_backend.tasks.gait)가 합니다 — 여기는 인증·소유권·record/job
# lifecycle·presigned 발급뿐이고 **영상 바이너리는 이 프로세스를 지나가지 않습니다.**
app.include_router(gait.router)
# 산책 기록(`/app/walks`). 라우터가 CurrentAppUser 로 잠겨 있습니다.
app.include_router(app_walks.router)
# 산책 기록을 조건별 공간 일기로 읽는 앱 전용 표면. 인증은 라우터가 받고,
# Place·Journey·Pin을 호출하지 않은 채 Walk 원판만 조립합니다 (D-049).
app.include_router(walk_spatial_diary.router)
app.include_router(training.router)
# 오케스트레이션 진입점 (Card 3). 인증은 `/training/chat` 과 같은 자리 —
# 엔드포인트 자체의 파라미터 의존성(`admin_or_app_user(Perm.READ)`)이 겁니다.
app.include_router(assistant.router)
# 크롤 관리 (RAG-047). 권한은 라우터 안에서 Perm 으로 겁니다 — 읽기 READ / 트리거 OPS_WRITE.
app.include_router(crawl.router)
app.include_router(screening_router)
# 실시간 산책 적합도. nginx 는 `:8000` 을 통째로 이 앱에 보내므로
# `daengback.~:8000/walk` 로 바로 나갑니다 (설정 변경 없음).
#
# **인증은 라우터가 아니라 여기서 겁니다.** `walk.router` 는 `daengs_life` 것이고,
# 저쪽이 `daengs_backend.core.deps` 를 import 하면 의존 방향이 뒤집혀
# `daengs_life.app.main`(단독 ASGI 앱)과 `python -m daengs_life.realtime walk` 가
# 이 레포의 인증 없이는 못 도는 물건이 됩니다 (RAG-001 원칙 1 · D-018).
# `include_router(dependencies=...)` 가 그 선을 넘지 않고 문을 잠그는 자리입니다.
#
# **앱 회원과 관리자를 함께 받습니다.** `#23` 은 `current_app_user` 로 앱 회원만 받았는데,
# 관리자도 콘솔에서 산책 판정을 확인할 수 있어야 해서 문을 넓혔습니다 (`#30`).
#
# 그래도 안전한 이유: `get_walk` 은 principal 을 **받기만 하고 쓰지 않습니다.** 좌표만
# 보고 답하므로, 관리자 `sub` 가 `app_users` 에 없어서 404/500 이 되는 자리가 없습니다.
# 신원으로 남의 것을 걸러야 하는 API 라면 이 의존성을 쓰면 안 됩니다.
#
# `Perm.READ` 는 VIEWER 까지 전부 가지므로 **로그인한 관리자면 누구나** 통과합니다.
# 권한을 좁히고 싶으면 여기 한 곳만 고치면 됩니다.
#
# ⚠ `/training/chat` 과는 **결론이 다릅니다.** 저쪽은 `#25` 가 만든 임시 게이트웨이라
# 앱 클라이언트가 없어서 관리자 전용으로 좁혔습니다 (`routers/training.py`).
app.include_router(walk.router, dependencies=[Depends(admin_or_app_user(Perm.READ))])

# 제도·문서형 질의응답. **`/walk` 과 같은 판단입니다** (메모 ⑦) — 인증을 라우터가 아니라
# 등록 시점에 걸고, 앱 회원과 관리자를 함께 받습니다.
#
# `post_ask` 도 principal 을 **받지 않으므로** 관리자 `sub` 가 `app_users` 에 없어서 깨지는
# 자리가 없습니다. 질문만 보고 답하는 API 라 신원으로 남의 것을 걸러야 할 일이 없습니다.
app.include_router(ask.router, dependencies=[Depends(admin_or_app_user(Perm.READ))])


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
