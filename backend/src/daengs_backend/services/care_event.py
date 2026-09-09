"""케어 로그(밥 · 약 · 간식)의 규칙. 트랜잭션 경계도 여기입니다 (#332).

라우터는 HTTP 만 보고, 리포지토리는 쿼리만 합니다. "이 강아지가 내 것인가"·"같은 기록을
두 번 받았나"·"기간이 너무 넓은가"·"하루의 경계가 어디인가" 는 전부 여기 모입니다.

**오케스트레이터를 모릅니다.** 비서가 이 로그를 읽는 것은 후속 카드이고, 채팅으로 기록하는
쓰기 능력은 처음부터 안 합니다 (2026-09-08 사람 결정 — #331 메모).
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import CareEvent
from daengs_backend.repositories import care_event as care_repo
from daengs_backend.repositories import pet as pet_repo
from daengs_backend.repositories import walk as walk_repo
from daengs_backend.schemas.care_event import CareEventCreate
from daengs_backend.services.pet import PetNotFoundError

log = logging.getLogger(__name__)

#: 기간 조회의 창 상한. 저장소 탭이 그리는 것은 하루·한 주이고, 한 달을 넘기면 화면이 아니라
#: 내보내기입니다 — 그 용도는 아직 없고, 상한 없이 열어 두면 한 요청이 몇 년치를 끌어옵니다.
MAX_RANGE = timedelta(days=31)
#: `from`/`to` 를 안 보냈을 때의 창.
DEFAULT_RANGE = timedelta(days=7)

#: 하루의 경계를 정하는 시간대. **앱이 보내지 않으면 서울입니다.**
#:
#: 서버 시간(UTC)으로 자르면 밤 9시 이후의 저녁밥이 "내일" 로 갑니다. 여행 중인 사용자까지
#: 맞추려면 앱이 보내야 하는데(`tz` 쿼리), 그것은 앱 #201 이 필요해지면 그때 더합니다 —
#: 지금 요청마다 받게 하면 앱이 늘 같은 값을 보내는 칸이 하나 늡니다.
DAY_TIMEZONE = "Asia/Seoul"


class CareEventNotFoundError(Exception):
    """내 기록이 아니거나 없습니다. 남의 것일 때도 이 예외입니다 (`PetNotFoundError` 와 같은 이유)."""


class CareRangeError(ValueError):
    """조회 창이 뒤집혔거나 상한을 넘었습니다. 라우터가 422 로 바꿉니다."""


@dataclass(frozen=True)
class DaySummary:
    day: date
    timezone: str
    start: datetime
    end: datetime
    counts: dict[str, int]
    walks: int
    events: list[CareEvent]


async def _owned_pet(session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID):
    """내 강아지가 아니면 404 감. **배웅한 아이도 통과합니다** — 배웅은 행을 안 지우고,
    있었던 일을 적어 두는 것이라 그 아이의 기록은 계속 보이고 남길 수 있어야 합니다."""
    pet = await pet_repo.get_owned(session, app_user_id, pet_id)
    if pet is None:
        raise PetNotFoundError
    return pet


async def record(
    session: AsyncSession, app_user_id: uuid.UUID, body: CareEventCreate
) -> tuple[CareEvent, bool]:
    """기록. **같은 멱등키가 이미 있으면 있던 것을 그대로 돌려줍니다.**

    앱은 탭 두 번·네트워크 재시도로 같은 것을 다시 보냅니다. 그때 두 줄이 되면 "밥 2번" 이
    되고 아무 에러도 안 납니다. 그래서 `client_event_id` 로 먼저 찾고, DB 의 UNIQUE 가 경쟁까지
    막습니다 — 경쟁에서 진 쪽은 IntegrityError 를 받고 이긴 쪽의 행을 돌려줍니다.

    **덮어쓰지 않습니다.** 같은 키로 다른 내용이 와도 먼저 온 것이 남습니다 — 재시도는
    같은 내용이라야 하고, 고치려면 지우고 다시 적습니다.

    :returns: (기록, 이번에 새로 만들었는가)
    """
    pet = await _owned_pet(session, app_user_id, body.pet_id)

    existing = await care_repo.get_by_client_event(session, pet.id, body.client_event_id)
    if existing is not None:
        return existing, False

    event = CareEvent(
        actor_app_user_id=app_user_id,
        pet_id=pet.id,
        kind=body.kind,
        occurred_at=body.occurred_at,
        note=body.note,
        client_event_id=body.client_event_id,
    )
    care_repo.add(session, event)
    try:
        await session.commit()
    except IntegrityError:
        # 같은 키가 동시에 두 번 온 경쟁. 진 쪽입니다 — 이긴 쪽의 행을 돌려줍니다.
        await session.rollback()
        winner = await care_repo.get_by_client_event(session, pet.id, body.client_event_id)
        if winner is None:  # pragma: no cover — UNIQUE 가 터졌는데 행이 없을 수는 없습니다
            raise
        return winner, False
    return event, True


def _window(start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    """조회 창. 안 보낸 쪽을 채우고 상한을 봅니다."""
    end = end or datetime.now(UTC)
    start = start or end - DEFAULT_RANGE
    if end <= start:
        raise CareRangeError("to 는 from 보다 뒤여야 합니다.")
    if end - start > MAX_RANGE:
        raise CareRangeError(f"조회 기간은 {MAX_RANGE.days}일까지입니다.")
    return start, end


async def list_events(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    pet_id: uuid.UUID,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> tuple[list[CareEvent], datetime, datetime]:
    """기간 조회. 창을 같이 돌려주는 이유는 기본값을 앱이 되짚어 볼 수 있게 하려는 것입니다."""
    await _owned_pet(session, app_user_id, pet_id)
    start, end = _window(start, end)
    return await care_repo.list_between(session, app_user_id, pet_id, start, end), start, end


async def delete_event(
    session: AsyncSession, app_user_id: uuid.UUID, event_id: uuid.UUID
) -> None:
    event = await care_repo.get_owned(session, app_user_id, event_id)
    if event is None:
        raise CareEventNotFoundError
    await care_repo.delete(session, event)
    await session.commit()


def day_bounds(day: date, timezone: str = DAY_TIMEZONE) -> tuple[datetime, datetime]:
    """그 날짜의 `[자정, 다음 자정)` — 시간대 기준. 다음 날 자정을 따로 만들어 DST 가 있는 지역도 맞춥니다."""
    tz = ZoneInfo(timezone)
    start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=tz)
    return start, end


async def day_summary(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    pet_id: uuid.UUID,
    *,
    day: date | None = None,
    timezone: str = DAY_TIMEZONE,
) -> DaySummary:
    """하루 요약 — "밥 2 · 약 1 · 간식 3 · 산책 1". 산책은 `walks` 에서 셉니다 (#332).

    `day` 를 안 보내면 그 시간대의 오늘입니다.
    """
    await _owned_pet(session, app_user_id, pet_id)
    day = day or datetime.now(ZoneInfo(timezone)).date()
    start, end = day_bounds(day, timezone)
    counts = await care_repo.count_by_kind(session, app_user_id, pet_id, start, end)
    walks = await walk_repo.count_for_pet_between(session, app_user_id, pet_id, start, end)
    events = await care_repo.list_between(session, app_user_id, pet_id, start, end)
    return DaySummary(
        day=day, timezone=timezone, start=start, end=end,
        counts=counts, walks=walks, events=events,
    )


__all__ = [
    "DAY_TIMEZONE",
    "DEFAULT_RANGE",
    "MAX_RANGE",
    "CareEventNotFoundError",
    "CareRangeError",
    "DaySummary",
    "day_bounds",
    "day_summary",
    "delete_event",
    "list_events",
    "record",
]
