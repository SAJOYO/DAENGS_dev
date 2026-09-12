"""활성 반려견 → 비서가 받아도 되는 오늘의 산책 요약 (D-073).

`services/care_log_context.py` 의 형제이고 규칙이 같습니다 — **소유권을 확인해 읽고, 좁혀서
넘기고, 못 채우면 조용히 None**. 넘어가는 모양은 `orchestration.contracts` 의
`WalkActivityContext` 입니다.

**좌표는 한 칸도 안 넘깁니다.** 이 표에는 GPS 청크가 있지만 넘기는 것은 합계뿐입니다 —
이유는 `WalkActivityContext` 독스트링에 있고, 한 줄로 줄이면 D-051 입니다.

**하루 경계를 케어 로그와 같이 씁니다** (`care_service.DAY_TIMEZONE`). 두 요약이 한 프롬프트에
같이 실리는데 서로 다른 "오늘" 을 말하면, 모델이 그것을 두 개의 사실로 읽습니다. 경계는
`care_service.day_bounds` 로 만듭니다 — tz-aware 서울 자정을 직접 다시 만들면 언젠가
naive datetime 이 섞여 `walks.started_at`(timestamptz) 을 서버 TZ 로 해석하게 둘 자리가
생깁니다.

**소유권은 `pet_repo.get_accessible` 로 확인합니다.** `activity_for_pet_between` 은
소유자 조건을 안 걸고 `pet_id` 만으로 찾으므로(그 독스트링에 이유가 있습니다), 남의
강아지 id 로 불러도 리포지토리 선에서는 안 걸립니다 — 여기서 먼저 구성원인지 봅니다.

**빈 날은 None 입니다.** 기록이 0건인 것은 "안 걸었다" 가 아니라 "산책 기록을 안 쓴다" 일
수 있습니다. 어느 쪽인지 모르는 채로 빈 블록을 실으면 모델이 둘 중 하나로 읽습니다.

**DB 오류도 None 입니다.** `care_log_context` 와 같습니다 — `walk_analyses`·
`activity_walk_heads` 가 서버 DB 에 아직 없을 수 있고(마이그레이션 버전 표가 없어
무엇이 적용됐는지 DB 가 기억하지 않습니다), 기록을 못 읽었다고 답할 수 있는 질문을
실패시키지 않습니다.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.repositories import pet as pet_repo
from daengs_backend.repositories import walk as walk_repo
from daengs_backend.services import care_event as care_service

log = logging.getLogger(__name__)

__all__ = ["resolve"]


async def resolve(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    active_dog_id: str,
    *,
    today: date | None = None,
) -> dict[str, object] | None:
    """`context["walk_activity"]` 에 넣을 값. **없으면 None 이고, 그것은 오류가 아닙니다.**

    id 가 UUID 가 아니거나 **구성원이 아닌** 강아지면 조용히 None 입니다. `today` 는 테스트가
    시계를 고정하는 자리이고, 안 주면 서울 기준 오늘입니다 (`care_service.DAY_TIMEZONE`).
    """
    try:
        pet_id = uuid.UUID(active_dog_id)
    except (ValueError, AttributeError, TypeError):
        return None

    pet = await pet_repo.get_accessible(session, app_user_id, pet_id)
    if pet is None:
        return None

    if today is None:
        today = datetime.now(ZoneInfo(care_service.DAY_TIMEZONE)).date()
    start, end = care_service.day_bounds(today)

    try:
        sums = await walk_repo.activity_for_pet_between(session, pet_id, start, end)
    except SQLAlchemyError as exc:
        # 표가 아직 없거나 DB 가 잠깐 아플 때. 비서는 로그 없이 답합니다.
        log.warning("산책 요약을 못 읽어 로그 없이 답합니다: %s", exc)
        return None

    if sums.walk_count == 0:
        return None

    resolved: dict[str, object] = {
        "day": today.isoformat(),
        "walk_count": sums.walk_count,
        "measured_walk_count": sums.measured_walk_count,
        "distance_m": sums.distance_m,
        "moving_s": sums.moving_s,
    }
    if sums.last_started_at is not None:
        zone = start.tzinfo
        resolved["last_started_at"] = sums.last_started_at.astimezone(zone).strftime("%H:%M")
    return resolved
