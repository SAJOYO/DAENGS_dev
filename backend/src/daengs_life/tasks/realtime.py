"""실시간 프리페치 태스크 (RT-001 ④-d · ④-e · ④-f).

**여기 있는 것은 스케줄과 예산 판단뿐이다.** 무엇을 어떻게 받는지는 전부 `realtime.collect`
에 있고, 프리페치는 요청 경로와 **같은 함수**를 탄다 — 전용 경로를 따로 두면 새 provider 를
요청 경로에만 붙이는 날 프리페치만 조용히 낡는다.
"""
from __future__ import annotations

import logging
from datetime import datetime

from daengs_life.realtime.cache import Cache
from daengs_life.realtime.collect import GRID_FEEDS, active_grids, warm
from daengs_life.realtime.config import KST

from .celery_app import app

log = logging.getLogger(__name__)


@app.task(name="daengs_life.tasks.realtime.warm_active")
def warm_active() -> dict[str, object]:
    """활성 격자 상위 `N` 개를 데운다 (④-d · ④-f).

    **④-e 3번의 안전장치가 여기다** — 예산이 소진되면 프리페치를 멈추고 pull 만 남긴다.
    한도 초과로 전부 죽는 것보다 낫고, 그때도 요청 경로는 캐시된 값(⑤-c stale)으로 답한다.
    """
    cache = Cache()
    now = datetime.now(KST)
    grids = active_grids(cache, now)
    if not grids:
        return {"grids": 0, "note": "활성 격자 없음 — 아직 아무도 요청하지 않았다"}

    # 예산은 **격자 feed 기준**으로 본다. ④-e 가 병목이라고 지목한 것이 그 서비스다
    # (실황+초단기예보+단기예보가 한 활용신청이라 1,000 을 셋이 나눠 쓴다).
    left = cache.budget_left(next(iter(GRID_FEEDS)), now)
    if left is not None and left <= 0:
        log.warning("일 예산 소진 — 프리페치 중단, pull 만 남긴다 (④-e 3)")
        return {"grids": 0, "budget_left": 0, "note": "예산 소진 — 프리페치 중단"}

    warmed, failed = 0, 0
    for grid in grids:
        try:
            warm(grid, now, cache=cache)
            warmed += 1
        except Exception:                       # noqa: BLE001 — 한 격자가 죽어도 나머지는 데운다
            log.exception("격자 %s 프리페치 실패", grid)
            failed += 1
    return {"grids": warmed, "failed": failed,
            "budget_left": cache.budget_left(next(iter(GRID_FEEDS)), now)}


__all__ = ["warm_active"]
