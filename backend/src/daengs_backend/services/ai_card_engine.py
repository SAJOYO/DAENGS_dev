"""backend 가 도감 카드 생성(`daengs_cardimage`)을 부르는 **유일한 자리** (D-076).

설정을 읽어 엔진·검수를 만들고, `generate_card` 에 설정값을 넣어 부른다. 관리자 콘솔
(`routers/admin_cardimage.py`)과 앱 경로(`services/ai_card.py`)가 둘 다 이것을 쓴다.

나중에 생성을 별도 서비스(Cloud Run 등)로 떼면 **이 모듈 안에서** HTTP 호출로 갈라진다 —
D-070 이 `DAENGS_REALTIME_URL` 값에 따라 같은 프로세스 호출과 HTTP 를 가른 것과 같은 모양이다.
그러니 다른 모듈이 `daengs_cardimage.generate_card` 나 Gemini 어댑터를 직접 부르게 하지 말 것.
"""

from __future__ import annotations

import random

from daengs_backend.config import settings
from daengs_backend.services import realtime_client
from daengs_cardimage import (
    CardImageUnavailable,
    GeneratedCard,
    catalog,
    generate_card,
)
from daengs_cardimage import plan_seeds as _plan_seeds
from daengs_cardimage.engine import CardImageEngine, GeminiCardImageEngine, HttpCardImageEngine
from daengs_cardimage.judge import CardJudge, GeminiCardJudge


def gpu_path_active() -> bool:
    """`FLUX.2-klein-4B` GPU 서비스 경로(D-078)가 켜졌나 — `DAENGS_CARDGEN_URL` 을 `strip()` 한 값이
    비어 있지 않으면 참이다. 거짓이면 Nano Banana 2 경로(지금 운영)다.

    **이 판정은 여기 한 곳뿐이다** (#572 Task 8). 엔진 선택(`default_engine`), 한 요청의 장수와 seed
    명시 여부(`plan_request_seeds`), `ready` 행에 seed 를 기록할지(`services/ai_card.py::_finish_ready`),
    정리 기준의 예산(`ai_card_quota.stale_after`), 키 확인(`ready_check`)이 전부 이것을 부른다 — 한쪽만
    `strip()` 을 빠뜨리면 공백뿐인 URL 에서 Nano Banana 2 로 두 장을 뽑거나 엔진이 버린 seed 를 기록하게
    된다."""
    return bool(settings.cardgen_url.strip())


#: 고를 수 있는 엔진 이름. `models/admin_ai_card.ADMIN_AI_CARD_ENGINES`(표의 CHECK)와 **같은 값이어야**
#: 한다 — 콘솔이 고른 이름이 그대로 `admin_ai_cards.engine` 에 들어간다 (#592).
ENGINE_NAMES: tuple[str, ...] = ("gemini", "cardgen")


def _gemini_engine() -> CardImageEngine:
    """Nano Banana 2 (지금 운영). 전역 `settings.gemini_api_key` 로 대체하지 않는다 —
    카드 생성 키는 `DAENGS_CARDIMAGE_GEMINI_API_KEY` 하나뿐이다."""
    return GeminiCardImageEngine(
        api_key=settings.cardimage_gemini_api_key.get_secret_value(),
        model=settings.cardimage_model,
        size=settings.cardimage_size,
        timeout_ms=settings.cardimage_timeout_ms,
    )


def _cardgen_engine() -> CardImageEngine:
    """FLUX.2-klein-4B GPU 서비스(D-078). `gpu_path_active()` 가 참일 때만 부른다 —
    `DAENGS_CARDGEN_URL` 이 비면 주소 없는 클라이언트가 만들어진다."""
    return HttpCardImageEngine(base_url=settings.cardgen_url.strip(), timeout_s=settings.cardgen_timeout_s,
                               auth=realtime_client.id_token)


def default_engine() -> CardImageEngine:
    """설정에서 실제 엔진을 만든다. `gpu_path_active()` 면 GPU 서비스(D-078), 아니면
    Nano Banana 2 — D-070 의 `DAENGS_REALTIME_URL` 갈림길과 같은 모양이다."""
    return _cardgen_engine() if gpu_path_active() else _gemini_engine()


def engine_by_name(name: str) -> CardImageEngine:
    """이름으로 엔진을 만든다 — **콘솔 전용**이다 (#592).

    앱 경로는 계속 `default_engine()` 을 쓴다(설정이 고른다). 콘솔만 둘을 나란히 견주므로
    사람이 고른 이름으로 만든다. 그래서 `gpu_path_active()` 가 참이어도 `gemini` 를 고르면
    Nano Banana 2 가 나온다 — `default_engine()` 으로는 표현할 수 없는 조합이다.

    `cardgen` 인데 `DAENGS_CARDGEN_URL` 이 비어 있으면 `CardImageUnavailable` 이다. 라우터가
    먼저 `gpu_path_active()` 를 보고 503 `cardgen_disabled` 로 막지만, 여기서도 막아야 주소
    없는 클라이언트가 만들어지지 않는다."""
    if name == "cardgen":
        if not gpu_path_active():
            raise CardImageUnavailable("DAENGS_CARDGEN_URL 이 비어 있습니다")
        return _cardgen_engine()
    if name == "gemini":
        return _gemini_engine()
    raise ValueError(f"모르는 엔진 이름입니다: {name!r}")


def default_judge() -> CardJudge:
    return GeminiCardJudge(
        api_key=settings.cardimage_gemini_api_key.get_secret_value(),
        model=settings.cardimage_judge_model,
        timeout_ms=settings.cardimage_timeout_ms,
    )


def generate(
    *,
    photo: bytes,
    content_type: str,
    card: catalog.CardSelector,
    dog_name: str,
    engine: CardImageEngine,
    judge: CardJudge | None,
    seed: int | None = None,
) -> GeneratedCard:
    """동기 호출(20~60초)이다. 이벤트 루프에서는 `asyncio.to_thread` 로 부른다.

    `card` 는 달 정수 또는 종류 문자열(딸기·상추)이다 — **앱 경로도 콘솔도 둘 다 넘긴다**
    (#593, D-085. #592 에서는 종류가 콘솔 전용이었다).

    `seed` 를 주면(#572 Task 4 fix round 1 controller ruling A — `start` 가 행마다 미리 뽑아 둔
    값, GPU 경로에서만) `generate_card` 가 그 값을 그대로, 재시도 없이 쓴다. 관리자 콘솔과 앱의
    Nano Banana 2 경로(#572 Task 8 — `plan_request_seeds` 가 `None` 을 준다)는 `seed` 없이 부른다
    (재시도 있는 옛 경로 그대로)."""
    return generate_card(
        photo=photo,
        content_type=content_type,
        card=card,
        dog_name=dog_name,
        engine=engine,
        judge=judge,
        base_dir=settings.cardimage_dir,
        open_months=settings.cardimage_months,
        judge_min=settings.cardimage_judge_min,
        seed=seed,
    )


def plan_seeds(card: catalog.CardSelector, count: int, rng: random.Random | None = None) -> list[int]:
    """`daengs_cardimage.plan_seeds` 로 위임한다 — 다른 모듈이 `daengs_cardimage` 를 직접 부르지
    않고 이 모듈 하나로 묶기 위해서다(모듈 docstring 참고). `/app/ai-cards` 가 요청을 받는
    순간(#572 Task 4 fix round 1) 이것으로 행마다 쓸 seed 를 미리, 한 번에 정한다."""
    return _plan_seeds(card, count, rng or random.Random())


def plan_request_seeds(card: catalog.CardSelector, rng: random.Random | None = None) -> list[int | None]:
    """앱 요청 하나에서 만들 카드마다 엔진에 넘길 seed. **길이가 곧 그 요청의 카드(행) 수다** (#572 Task 8,
    사용자 결정 2026-09-17).

    - **Nano Banana 2 경로**(`gpu_path_active()` 가 거짓, 지금 운영): `[None]` — 한 장, seed 를 명시하지
      않는다. 그래야 `generate_card` 가 seed 없는 `count == 1` 경로로 가서 첫 장의 닮음이
      `cardimage_judge_min` 미만이면 **한 번 더** 만든다. Nano Banana 2 는 seed 를 어차피 버린다. 두 장을
      안 뽑는 이유는 앱에 두 장 중 고르는 화면이 아직 없어서다 — 두 장은 그 화면과 함께 GPU 경로를 켤 때
      나간다.
    - **GPU 경로**(`FLUX.2-klein-4B`): `plan_seeds(card, cardimage_pick_count)` — 한 번에 뽑은 서로 다른
      seed 로 `cardimage_pick_count` 장(최대 2), 겹치지 않는 seed 가 모자라면 그만큼 적게. seed 를 명시하므로
      재시도는 없다."""
    if not gpu_path_active():
        return [None]
    return list(plan_seeds(card, settings.cardimage_pick_count, rng))


def ready_check(card: catalog.CardSelector) -> catalog.MonthCard:
    """**돈이 나가기 전에** 거를 수 있는 설정 문제를 먼저 본다.

    닫힌 달·없는 카드는 `MonthNotOpenError`, 키·틀·글꼴이 없으면 `CardImageUnavailable`. 앱 경로는
    이것을 행을 만들기 전에 불러, 어차피 실패할 요청이 한도를 먹거나 백그라운드로 가지 않게 한다.

    잠금(`DAENGS_CARDIMAGE_MONTHS`)은 **달일 때만** 본다 — 달이 아닌 카드(딸기·상추)에는 그런 설정이
    없고 **카탈로그에 있으면 열린 것**이다(`generate._setup` 과 같은 규칙, #593 에서 정함).
    """
    meta = (catalog.require_open(card, settings.cardimage_months) if isinstance(card, int)
            else catalog.resolve(card))
    # URL 이 있으면 생성엔 키가 필요 없지만 default_judge() 는 여전히 이 키를 쓴다 — 없으면 카드가 채점 없이 통과한다.
    if not gpu_path_active() and not settings.cardimage_gemini_api_key.get_secret_value().strip():
        raise CardImageUnavailable("DAENGS_CARDIMAGE_GEMINI_API_KEY 가 비어 있습니다")
    for path in (
        catalog.template_path(card, settings.cardimage_dir),
        catalog.font_path(settings.cardimage_dir),
    ):
        if not path.exists():
            raise CardImageUnavailable(f"카드 생성 자산이 없습니다: {path}")
    return meta
