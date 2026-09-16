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


def default_engine() -> CardImageEngine:
    """설정에서 실제 엔진을 만든다. `DAENGS_CARDGEN_URL` 이 있으면 GPU 서비스(D-078), 없으면
    Nano Banana 2 — D-070 의 `DAENGS_REALTIME_URL` 갈림길과 같은 모양이다.
    전역 `settings.gemini_api_key` 로 대체하지 않는다 — 카드 생성 키는 `DAENGS_CARDIMAGE_GEMINI_API_KEY` 하나뿐이다."""
    url = settings.cardgen_url.strip()
    if url:
        return HttpCardImageEngine(base_url=url, timeout_s=settings.cardgen_timeout_s,
                                   auth=realtime_client.id_token)
    return GeminiCardImageEngine(
        api_key=settings.cardimage_gemini_api_key.get_secret_value(),
        model=settings.cardimage_model,
        size=settings.cardimage_size,
        timeout_ms=settings.cardimage_timeout_ms,
    )


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
    month: int,
    dog_name: str,
    engine: CardImageEngine,
    judge: CardJudge | None,
    seed: int | None = None,
) -> GeneratedCard:
    """동기 호출(20~60초)이다. 이벤트 루프에서는 `asyncio.to_thread` 로 부른다.

    `seed` 를 주면(#572 Task 4 fix round 1 controller ruling A — `start` 가 행마다 미리 뽑아 둔
    값) `generate_card` 가 그 값을 그대로, 재시도 없이 쓴다. 관리자 콘솔은 여전히 `seed` 없이
    부른다(재시도 있는 옛 경로 그대로)."""
    return generate_card(
        photo=photo,
        content_type=content_type,
        month=month,
        dog_name=dog_name,
        engine=engine,
        judge=judge,
        base_dir=settings.cardimage_dir,
        open_months=settings.cardimage_months,
        judge_min=settings.cardimage_judge_min,
        seed=seed,
    )


def plan_seeds(month: int, count: int, rng: random.Random | None = None) -> list[int]:
    """`daengs_cardimage.plan_seeds` 로 위임한다 — 다른 모듈이 `daengs_cardimage` 를 직접 부르지
    않고 이 모듈 하나로 묶기 위해서다(모듈 docstring 참고). `/app/ai-cards` 가 요청을 받는
    순간(#572 Task 4 fix round 1) 이것으로 행마다 쓸 seed 를 미리, 한 번에 정한다."""
    return _plan_seeds(month, count, rng or random.Random())


def ready_check(month: int) -> catalog.MonthCard:
    """**돈이 나가기 전에** 거를 수 있는 설정 문제를 먼저 본다.

    닫힌 달은 `MonthNotOpenError`, 키·틀·글꼴이 없으면 `CardImageUnavailable`. 앱 경로는 이것을
    행을 만들기 전에 불러, 어차피 실패할 요청이 한도를 먹거나 백그라운드로 가지 않게 한다.
    """
    card = catalog.require_open(month, settings.cardimage_months)
    # URL 이 있으면 생성엔 키가 필요 없지만 default_judge() 는 여전히 이 키를 쓴다 — 없으면 카드가 채점 없이 통과한다.
    if not settings.cardgen_url.strip() and not settings.cardimage_gemini_api_key.get_secret_value().strip():
        raise CardImageUnavailable("DAENGS_CARDIMAGE_GEMINI_API_KEY 가 비어 있습니다")
    for path in (
        catalog.template_path(month, settings.cardimage_dir),
        catalog.font_path(settings.cardimage_dir),
    ):
        if not path.exists():
            raise CardImageUnavailable(f"카드 생성 자산이 없습니다: {path}")
    return card
