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
from daengs_backend.core.tracing import configure_tracing
from daengs_backend.core.warm_up import STATE_ATTR, WarmUp, WarmUpPhase, now
from daengs_backend.routers import (
    activity,
    admin_account,
    admin_audit,
    admin_cardimage,
    ai_card,
    app_auth,
    app_report,
    app_user_admin,
    assistant,
    auth,
    care_event,
    chat,
    crawl,
    dogcard,
    facility_discovery,
    gait,
    health,
    invite_web,
    life_walk,
    metrics,
    pet,
    pet_member,
    pet_walks,
    report_admin,
    status,
    territory,
    territory_bookmark,
    place_bookmark,
    territory_claim,
    territory_game,
    training,
    vet_visit,
    walk_diary_slots,
    walk_entry,
    walk_entry_v2,
    walk_motion,
    walk_photo,
    walk_spatial_diary,
    walk_storyboard,
)
from daengs_backend.routers import (
    # ⚠️ 별칭입니다. 아래 `daengs_screening.service` 의 `screening_router` 와 이름이
    #    겹칩니다 — 그쪽은 옛 무인증 `/screen/*`, 이쪽은 새 계약 `/app/screening/*`.
    screening as app_screening,
)

# ⚠️ 별칭입니다. 아래에서 `daengs_life` 의 `walk`(산책 **적합도**)를 같은 이름으로
# import 하는데, 그쪽이 나중에 와서 이걸 가려 버립니다. 모듈 이름은 여전히 겹치므로
# 별칭은 남깁니다 — 다만 **경로는 A4(#176)로 갈렸습니다**: 기록은 `/app/walks`,
# 적합도는 `/life/walk-conditions` 입니다.
from daengs_backend.routers import walk as app_walks
from daengs_backend.services.training_rag import release_training_runtime

# 이 앱이 `daengs_life` 를 부르는 **유일한 자리**입니다. D-018 이 일부러 안 그은 선을
# 여기서만 긋습니다 — 접점은 **등록 두 줄과 예열 한 줄**이 전부입니다.
#
# `/life/ask` 는 임베딩 모델을 씁니다. 그래도 여기 붙이는 것이 D-021 의 결정입니다 — 모델을
# 배포되는 API 프로세스에 그대로 상주시키고(약 2.4GB), 2단계에서 조건이 오면
# `daengs_life.app.main:app`(이미 독립 ASGI 앱)을 따로 띄우고 이 자리를 게이트웨이로 바꿉니다.
# 이사가 싼 채로 남으려면 **접점이 이 세 줄을 넘으면 안 됩니다.**
#
# 그래도 **import 는 여전히 가벼워야 합니다.** `daengs_life` 쪽이 torch·psycopg 를 전부
# 함수 안에서 부르므로 모듈을 읽는 것만으로는 아무것도 안 올라옵니다 — 그 사실을
# `tests/test_main_stays_light.py` 가 기계로 지킵니다. 무거워지는 것은 import 가 아니라
# 아래 lifespan 의 예열이고, 그래서 그것만 백그라운드로 돌립니다.
#
# `encoder_loaded` 는 **묻기만 합니다** — 예열이 끝난 뒤 성패를 가르려고 부릅니다 (#180).
# `get_encoder()` 로 물으면 안 올라와 있을 때 **올려 버립니다.** 새 파일이 아니라 이미
# 승인된 이 자리의 이름 하나라, D-035 의 경계 테스트는 그대로 통과합니다.
from daengs_life.app.controllers import ask, walk
from daengs_life.app.deps import (
    encoder_loaded,
    get_cache,
    release_encoder,
    warm_up_encoder,
)

# ⚠️ 스크리닝도 같은 규칙입니다 — 이 import 로 torch 가 딸려 오면 안 됩니다.
#    `service.py` 최상단은 fastapi 와 `agent`(config 만 씀)뿐이고, 가중치는
#    첫 요청 때 올라옵니다 (D-039).
from daengs_screening.service import router as screening_router

# 리로드 감시 대상. 폴링으로 도는 환경(컨테이너 + 바인드 마운트)에서
# 범위를 좁혀 두지 않으면 CPU 를 계속 씁니다.
SRC_DIR = Path(__file__).resolve().parents[1]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # **맨 앞이어야 합니다.** LangSmith 클라이언트는 모듈 전역 캐시라 먼저 만든 쪽이
    # 이깁니다 — 다른 코드가 트레이스를 하나라도 만든 뒤에 부르면 마스킹 없는
    # 클라이언트가 굳고, 신원·정밀 좌표가 그대로 나갑니다 (`core.tracing`).
    #
    # `LANGSMITH_TRACING` 이 없으면 아무것도 안 합니다. 기본은 꺼져 있습니다.
    configure_tracing()

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
    # 여기서 동기로 부르면 안 됩니다 — 이 프로세스에는 로그인·`/life/walk-conditions`·`/training` 이 같이
    # 살고, 가중치를 RAM 으로 올리는 5~7초 동안 **API 전체가 502** 입니다. 컨테이너가
    # `reload=True` 로 돌고 배포가 마운트된 소스를 갈아 끼우므로 그 일이 backend 코드가
    # 바뀌는 배포마다 일어납니다.
    #
    # 예열이 도는 동안 들어온 `/life/ask` 는 **기다리지 않고 즉시 503 + `Retry-After`** 입니다
    # (#37). 기다리게 하면 콜드 캐시에서 nginx 의 60초를 넘겨 사용자가 HTML 504 를 받습니다.
    # 그 판단은 여기가 아니라 `deps.get_encoder` 가 합니다 — 두 벌을 막는 `deps._ENCODER_LOCK`
    # 은 그대로 있고, 바뀐 것은 그 락을 **기다리는 방식**뿐입니다.
    #
    # `settings.warm_up_encoder` 로 끌 수 있습니다 — 테스트와 개발 PC 용입니다. 끄면 모델이
    # 안 뜨는 게 아니라 **첫 `/life/ask` 가 로드를 뭅니다.** 그때는 아무도 예열하고 있지 않으므로
    # 위의 503 이 아니라 기다리는 쪽이 맞습니다 (`deps._WARM_UP_IN_PROGRESS`).
    #
    # **결과를 `app.state` 에 적습니다** (#180). 상태 화면이 "모델이 올라왔나"를 물을 자리가
    # 여기밖에 없습니다 — `daengs_life.app.deps` 를 상태 라우터가 직접 읽으면 접점이
    # 셋에서 넷이 되고, `tests/test_main_stays_light.py` 가 거기서 깨집니다 (D-035).
    # **이미 예열을 부르고 있는 이 자리**가 그 결과를 남기면 접점은 안 늘어납니다.
    #
    # `warm_up_encoder()` 는 성공해도 실패해도 `None` 을 돌려주므로(lifespan 이 부르는
    # 함수라 예외를 안 던집니다), 끝난 뒤 `encoder_loaded()` 로 물어서 가릅니다 —
    # 그쪽은 **올리지 않고 물어보기만** 합니다.
    setattr(app.state, STATE_ATTR, WarmUp(phase=WarmUpPhase.DISABLED))

    async def _warm_up_and_record() -> None:
        setattr(app.state, STATE_ATTR, WarmUp(phase=WarmUpPhase.LOADING, started_at=now()))
        await asyncio.to_thread(warm_up_encoder)
        before = getattr(app.state, STATE_ATTR)
        phase = WarmUpPhase.READY if encoder_loaded() else WarmUpPhase.FAILED
        setattr(
            app.state,
            STATE_ATTR,
            WarmUp(phase=phase, started_at=before.started_at, finished_at=now()),
        )

    warm_up = asyncio.create_task(_warm_up_and_record()) if settings.warm_up_encoder else None

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
# 공동 돌봄 초대 링크의 웹 폴백(`/invite`) · App Links 검증(`/.well-known/assetlinks.json`).
# 인증도 DB 도 안 쓴다 — `pet_member.router` 의 `/app/pet-invites/*` 와는 완전히 별개다.
app.include_router(invite_web.router)
app.include_router(auth.router)
# 앱 회원(카카오)용. 관리자와 경로가 겹치지 않게 /auth/app/* 입니다.
app.include_router(app_auth.router)
# 강아지 프로필. 라우터 자체가 CurrentAppUser 로 잠겨 있습니다.
app.include_router(pet.router)
# 공동 돌봄 초대 · 수락 (docs/co-care.md §3). pet 라우터와 별도 파일인 이유는
# 수락 경로가 /app/pets/{pet_id}/... 아래가 아니기 때문입니다 — 수락 전에는
# 그 강아지에 아무 권한이 없어 URL 에 pet_id 를 실으면 안 됩니다.
app.include_router(pet_member.router)
# 산책 기록 공동 조회(`/app/pets/{pet_id}/walks`) — 읽기만. 쓰기·개인 목록은 `/app/walks` 그대로.
app.include_router(pet_walks.router)
# 산책 기록 화면의 통합 목록(`/app/pet-walks`) — 볼 수 있는 강아지 전부의 공동 보호자 산책·합계.
app.include_router(pet_walks.feed_router)
# 케어 로그(`/app/care-events` · #332) — 밥·약·간식 기록. 산책은 `walks` 가 진실이라 여기 없고,
# 하루 요약이 세어 같이 보여 줍니다. 오케스트레이터는 이 표를 아직 안 읽습니다(후속 카드).
app.include_router(care_event.router)
# 진료비 기록(`/app/vet-visits` · #353) — 영수증 사진에서 읽고, 유저가 확정한
# 것만 남긴다. 여섯 개 엔드포인트가 CurrentAppUser 로 잠겨 있습니다 — 예외는
# 사진 bridge 둘(`_bridge/upload`·`_bridge/download`)뿐이고, 그 둘은 추측 불가능한
# 키 + exclusive=True 한 번뿐인 쓰기로 안전을 대신합니다(라우터 머리말).
app.include_router(vet_visit.router)

# 도감 카드 (D-052). 앱이 Room 과 filesDir 에만 갖고 있던 것을 서버로 —
# 그전까지는 폰을 바꾸면 뽑은 카드가 전부 사라졌습니다.
app.include_router(dogcard.router)
# 콘솔의 「도감 카드 생성」 탭이 부르는 점검 경로 (#496) — 저장하지 않는다.
app.include_router(admin_cardimage.router)
# 앱 사용자가 AI 도감 카드를 만들고 조회·삭제하는 경로 (#537, D-076). 비동기 — POST 는
# 202 로 시작만 알리고, 생성은 같은 프로세스의 백그라운드 작업이 한다.
app.include_router(ai_card.router)
# 보행 분석 orchestration (D-043). 라우터가 CurrentAppUser 로 잠겨 있고, 분석 자체는
# 별도 워커(daengs_backend.tasks.gait)가 합니다 — 여기는 인증·소유권·record/job
# lifecycle·presigned 발급뿐이고 **영상 바이너리는 이 프로세스를 지나가지 않습니다.**
app.include_router(gait.router)
# 산책 기록(`/app/walks`). 라우터가 CurrentAppUser 로 잠겨 있습니다.
app.include_router(walk_motion.router)
app.include_router(app_walks.router)
app.include_router(walk_entry.router)
app.include_router(walk_entry_v2.capabilities_router)
app.include_router(walk_entry_v2.router)
app.include_router(walk_storyboard.router)
app.include_router(walk_diary_slots.router)
app.include_router(walk_photo.router)
# 산책 중 점령지 촬영 인증. 위치 10m만 동기로 확인하고 사진 판정은 비동기 상태로 둡니다.
app.include_router(territory.router)
app.include_router(territory_claim.router)
app.include_router(territory_game.router)
app.include_router(territory_bookmark.router)
app.include_router(place_bookmark.router)
app.include_router(activity.router)

# 피부 변화 기록 (D-052). **옛 `/screen/v1/screen` 과 다른 경로입니다** —
# 그쪽은 인증 없이 판정만 하고 아무것도 안 남기며, 앱이 아직 그것을 씁니다.
# 여기는 인증·소유권·사진 저장이 붙은 새 계약이고, 옛 경로를 410 으로 닫는 것은
# 앱이 옮겨간 뒤 별도 카드입니다 (보행 `/gait/*` → `/app/gait/*` 와 같은 방식).
app.include_router(app_screening.router)
# 산책 기록을 조건별 공간 일기로 읽는 앱 전용 표면. 인증은 라우터가 받고,
# Place·Journey·Pin을 호출하지 않은 채 Walk 원판만 조립합니다 (D-049).
app.include_router(walk_spatial_diary.router)
app.include_router(training.router)
# 오케스트레이션 진입점 (Card 3). 인증은 `/training/chat` 과 같은 자리 —
# 엔드포인트 자체의 파라미터 의존성(`admin_or_app_user(Perm.READ)`)이 겁니다.
app.include_router(assistant.router)
app.include_router(facility_discovery.router)
# 대화 기록(`/app/chats`)과 저장된 AI 요약. 라우터가 CurrentAppUser 로 잠겨 있습니다 —
# 신원으로 남의 것을 걸러야 해서 `admin_or_app_user` 를 쓰지 않습니다 (core/deps.py).
app.include_router(chat.router)
# AI 답변 신고 접수 (`/app/reports` · A1 · D-053). `CurrentAppUser` 라 **본인 대화의
# turn 만** 신고할 수 있습니다 — 남의 turn_id 는 404 입니다. 답변 원문은 받지 않습니다
# (turn_id 가 chat_turns 를 가리킵니다 — D-048).
app.include_router(app_report.router)
# 크롤 관리 (RAG-047). 권한은 라우터 안에서 Perm 으로 겁니다 — 읽기 READ / 트리거 OPS_WRITE.
app.include_router(crawl.router)
# 운영 지표 (#223 · 콘솔 로드맵 B3). 제품 테이블(chat_*)을 세기만 하고 **원문은 스키마에
# 담을 칸조차 없습니다** (D-037). 권한이 `metrics:read` 라 VIEWER 만 막힙니다 —
# `ANALYST` 라는 role 이 존재하는 이유가 이 화면입니다.
app.include_router(metrics.router)
# 감사 로그 조회 (#221 · 콘솔 로드맵 A4-1). **읽기 전용이고 이 조회 자체는 감사에 남기지
# 않습니다** — 남기면 화면이 자기 기록으로 채워지고 그 행을 본 것도 남겨야 하는 재귀가
# 됩니다. 권한은 `ADMIN_MANAGE` 라 OPERATOR 는 복호화는 해도 누가 했는지는 못 봅니다.
app.include_router(admin_audit.router)
# 관리자 계정 관리 (#207 · 콘솔 로드맵 A3). 권한은 라우터 안에서 `ADMIN_MANAGE` 로 겁니다 —
# D-014 의 role 5단계가 실제로 갈리는 첫 자리입니다 (그 전까지는 정의만 있었습니다).
app.include_router(admin_account.router)
# 회원 조회 (#211 · 콘솔 로드맵 A2). **위 줄과 다른 사람들입니다** — `admin_users` 는 이
# 콘솔에 로그인하는 사내 계정이고, `app_users` 는 앱을 쓰는 회원입니다 (03_auth.sql).
# `/app/*` 와도 다른 문입니다: 저기는 앱 회원이 자기 것을 보고 여기는 관리자가 남의 것을
# 봅니다. 나가는 개인정보는 전부 마스킹이라 권한이 `READ` 이고, 원문을 여는 문은 짝
# 카드(#212)가 `pii:read` 로 따로 냅니다.
app.include_router(app_user_admin.router)
# 신고 조회·처리 (`/admin/reports` · A1 · D-053). 권한은 `ADMIN_MANAGE` 입니다 — 신고된
# 답변을 여는 것은 **회원의 대화 원문을 보는 일**이라, 계정 관리와 같은 등급으로 묶었습니다.
# **목록은 감사에 안 남기고 상세만 남깁니다** — `pii_revealed` 가 그은 선과 같습니다.
app.include_router(report_admin.router)
# 상태 페이지 (#180 · 콘솔 로드맵 B1). 읽기 전용이고 DB 를 바꾸지 않습니다.
# `/health` 와 다른 자리입니다 — 저기는 모니터링이 읽고 DB 가 죽으면 503 이며,
# 여기는 사람이 읽고 항목 하나가 죽어도 200 으로 나머지를 보여 줍니다.
app.include_router(status.router)
app.include_router(screening_router)
# 실시간 산책 적합도. nginx 는 `:8000` 을 통째로 이 앱에 보내므로
# `daengback.~:8000/life/walk-conditions` 로 바로 나갑니다 (설정 변경 없음 — A4(#176)로
# 경로가 바뀌어도 nginx 에는 이 경로의 location 이 없어 "그 외 → backend" 로 갑니다).
#
# **인증은 라우터가 아니라 여기서 겁니다.** `walk.router` 는 `daengs_life` 것이고,
# 저쪽이 `daengs_backend.core.deps` 를 import 하면 의존 방향이 뒤집혀
# `daengs_life.app.main`(단독 ASGI 앱)과 `python -m daengs_life.realtime walk` CLI 가
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
# 🔴 D-070 — 분리 갈림길. `DAENGS_REALTIME_URL` 이 있으면 이 앱은 **판정을 하지 않고
# 전달만** 합니다. 인증은 두 갈래에서 **같은 의존성**이라 앱 회원·관리자 판정이 안 갈립니다.
#
# 비어 있을 때의 줄은 분리 전과 **글자 그대로 같습니다** — 그래야 되돌리기가 변수 하나가 되고,
# 개발 PC·개발서버의 기존 동작과 테스트가 안 바뀝니다.
if settings.realtime_url:
    app.include_router(life_walk.router, dependencies=[Depends(admin_or_app_user(Perm.READ))])
else:
    app.include_router(walk.router, dependencies=[Depends(admin_or_app_user(Perm.READ))])

# 제도·문서형 질의응답. **`/life/walk-conditions` 와 같은 판단입니다** (메모 ⑦) — 인증을 라우터가 아니라
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
