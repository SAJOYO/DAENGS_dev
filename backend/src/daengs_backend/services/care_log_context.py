"""활성 반려견 → 비서가 받아도 되는 오늘의 케어 요약 (#344).

`services/dog_context.py` 와 같은 층이고 같은 규칙입니다 — **소유권을 확인해 읽고, 좁혀서
넘기고, 못 채우면 조용히 None**. 넘어가는 모양의 정의는 `orchestration.contracts` 의
`CareLogContext` 이고, 이 파일은 그 모양을 `services/care_event.day_summary` 에서 만듭니다.

**여기서 멈추는 것이 설계입니다.** 하루 요약에는 이벤트 목록과 `note` 가 있지만 둘 다 안
넘깁니다. 비서가 하려는 말은 "오늘 아침 약이 아직 체크 안 됐어요" 이고, 그 문장에 필요한 것은
종류별 건수와 마지막 시각뿐입니다. `note` 는 사용자가 적은 자유 텍스트라 프롬프트의 지시문
옆에 놓이면 지시문처럼 읽힐 수 있습니다.

**빈 날은 None 입니다.** 오늘 기록이 0건인 것은 "아직 안 챙겼다" 가 아니라 "이 기능을 안
쓴다" 일 수 있습니다. 어느 쪽인지 모르는 채로 프롬프트에 빈 블록을 실으면 모델이 둘 중 하나로
읽습니다 — 안 실으면 이 카드 전과 똑같이 답합니다.

**DB 오류도 None 입니다.** `care_events` 표는 #332 의 마이그레이션이 서버 DB 에 적용돼야
생기고, 그것은 DB 담당자의 일이라 이 코드가 먼저 배포될 수 있습니다. 그때 조회는
`ProgrammingError` 를 내는데, 로그를 못 읽었다고 답할 수 있는 질문을 실패시키지 않습니다 —
경고 한 줄을 남기고 로그 없이 답합니다.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from zoneinfo import ZoneInfo

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.services import care_event as care_service
from daengs_backend.services.pet import PetNotFoundError

log = logging.getLogger(__name__)

__all__ = ["resolve"]

#: 마지막 시각을 내는 종류. 산책은 `walks` 에서 건수만 세므로(#332) 시각이 없습니다.
_TIMED_KINDS = ("meal", "medication", "snack")


async def resolve(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    active_dog_id: str,
    *,
    today: date | None = None,
) -> dict[str, object] | None:
    """`context["care_log"]` 에 넣을 값. **없으면 None 이고, 그것은 오류가 아닙니다.**

    id 가 UUID 가 아니거나 남의 강아지면 조용히 None 입니다 — 소유권은 `day_summary` 가
    `pet_repo.get_owned` 로 묶고 있어 남의 id 로는 못 읽습니다. `today` 는 테스트가 시계를
    고정하는 자리이고, 안 주면 서울 기준 오늘입니다 (`care_service.DAY_TIMEZONE`).
    """
    try:
        pet_id = uuid.UUID(active_dog_id)
    except (ValueError, AttributeError, TypeError):
        return None
    try:
        summary = await care_service.day_summary(session, app_user_id, pet_id, day=today)
    except PetNotFoundError:
        return None
    except SQLAlchemyError as exc:
        # 표가 아직 없거나 DB 가 잠깐 아플 때. 비서는 로그 없이 답합니다.
        log.warning("care_events 요약을 못 읽어 로그 없이 답합니다: %s", exc)
        return None

    counts = {kind: int(summary.counts.get(kind, 0)) for kind in _TIMED_KINDS}
    counts["walk"] = int(summary.walks)
    if not any(counts.values()):
        return None

    resolved: dict[str, object] = {"day": summary.day.isoformat(), **counts}
    zone = ZoneInfo(summary.timezone)
    for kind in _TIMED_KINDS:
        latest = max(
            (event.occurred_at for event in summary.events if event.kind == kind),
            default=None,
        )
        if latest is not None:
            resolved[f"last_{kind}_at"] = latest.astimezone(zone).strftime("%H:%M")
    return resolved
