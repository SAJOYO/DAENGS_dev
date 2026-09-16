"""케어 로그(밥 · 약 · 간식)의 규칙. 트랜잭션 경계도 여기입니다 (#332).

라우터는 HTTP 만 보고, 리포지토리는 쿼리만 합니다. "이 강아지를 내가 돌보는가"·"같은 기록을
두 번 받았나"·"기간이 너무 넓은가"·"하루의 경계가 어디인가" 는 전부 여기 모입니다.

**오케스트레이터를 모릅니다 — 그런데 오케스트레이터가 이 파일을 부릅니다.** 비서가 로그를
읽는 것은 `services/care_log_context`(#344), 쓰는 것은
`orchestration/adapters/care_log.py`(D-075)이고, 후자는 아래 `record` 를 **라우터와 같은
함수로** 부릅니다. 방향이 한쪽인 것이 중요합니다: 이 파일은 여전히 orchestration 을 import
하지 않고, 채팅에서 온 기록이 화면에서 온 기록과 다른 규칙을 통과할 길이 없습니다.

2026-09-08 의 사람 결정("채팅으로 기록하는 쓰기 능력은 처음부터 안 한다" — #331 메모)은
2026-09-13 에 D-075 로 개정됐습니다. 그 메모가 같이 적어 둔 순서의 세 번째 칸이고, "처음부터
안 한다" 가 가리킨 것은 **확인 없는** 쓰기였습니다.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import CareEvent
from daengs_backend.repositories import care_event as care_repo
from daengs_backend.repositories import pet as pet_repo
from daengs_backend.repositories import walk as walk_repo
from daengs_backend.schemas.care_event import CareEventCreate
from daengs_backend.services import pet_identity as identity_service
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

#: 약 중복 확인 창. 새 기록의 occurred_at 앞뒤로 이만큼 안에 다른 약 기록이 있으면 확인을 받는다.
#:
#: **하루(서울 자정) 가 아닌 이유**는 1일 2회 투약이 정상이기 때문이다 — 하루로 잡으면 저녁 약마다
#: 경고가 떠서 사람이 경고를 안 읽고 누르는 습관이 든다. 실제 위험은 교대 경계의 짧은 중복이라
#: 6시간이면 잡히고 12시간 간격은 안 걸린다 (docs/co-care.md §4).
MEDICATION_CONFIRM_WINDOW = timedelta(hours=6)

#: 확인을 받는 종류. **밥·간식은 안 받는다** — 한 번 더 줘도 위험하지 않고, 경고가 잦으면 정작
#: 약 경고가 안 읽힌다. 종류를 늘리려면 이 집합만 고친다.
CONFIRM_KINDS = frozenset({"medication"})


class CareEventNotFoundError(Exception):
    """내 기록이 아니거나 없습니다. 남의 것일 때도 이 예외입니다 (`PetNotFoundError` 와 같은 이유)."""


class MedicationConflictError(Exception):
    """창 안에 이미 같은 종류가 있습니다. `confirm=True` 로 다시 보내면 기록됩니다."""

    def __init__(self, conflicts: list[CareEvent]) -> None:
        self.conflicts = conflicts
        super().__init__(f"{len(conflicts)}건")


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
    #: 이 요약이 읽은 **논리 그룹의 pet id 들**. 라우터가 이름표 판정을 그룹 전체로
    #: 하는 데 씁니다 (`pet_member.group_actor_label`) — 안 넘기면 연결된 상대가 적은
    #: 기록의 이름이 통째로 빕니다.
    group_ids: list = field(default_factory=list)
    #: 그날 그 논리 강아지가 나간 산책 행들. **누가 다녀왔는지**(`walk.app_user_id`)가
    #: 여기 있습니다 — 라우터가 케어 `actor` 와 같은 규칙으로 이름표를 붙입니다
    #: (MVP 결정 §7). `walks` 는 그 수이고 둘이 같은 질의에서 나옵니다.
    walk_rows: list = field(default_factory=list)


async def _accessible_pet(session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID):
    """**구성원(대표 ∪ 돌보미)이 아니면** 404 감 (docs/co-care.md §2).

    돌보미가 기록하고 대표가 보는 것이 이 기능의 전부라, 기록·조회는 대표 기준이면
    안 됩니다. 대신 여기를 지나도 **고치거나 지우지는 못합니다** — 그쪽은 `pet_repo.get_owned`
    를 그대로 씁니다.

    **배웅한 아이도 통과합니다** — 배웅은 행을 안 지우고, 있었던 일을 적어 두는 것이라
    그 아이의 기록은 계속 보이고 남길 수 있어야 합니다.
    """
    pet = await pet_repo.get_accessible(session, app_user_id, pet_id)
    if pet is None:
        raise PetNotFoundError
    return pet


async def _group_ids(session: AsyncSession, pet) -> list[uuid.UUID]:
    """읽을 때 합칠 pet id 들 (MVP 결정 §7).

    연결 안 된 아이는 `[pet.id]` 하나라 지금 동작이 안 바뀝니다. 연결됐으면 같은 실제
    강아지의 행 전부입니다 — 기록이 두 `pet_id` 에 갈려 쌓이는데 화면에서는 한 마리이기
    때문입니다.

    **쓰기에는 안 씁니다.** 새 기록은 언제나 부른 사람의 행(`pet.id`)에 남습니다 — 그래야
    연결을 풀었을 때 각자가 적은 것이 제자리에 남습니다.
    """
    return await identity_service.group_pet_ids_of(session, pet)


async def group_ids_for(
    session: AsyncSession, app_user_id: uuid.UUID, pet_id: uuid.UUID
) -> list[uuid.UUID]:
    """접근 권한을 확인하고 그 아이의 **논리 그룹 pet id 들**을 돌려줍니다.

    라우터가 이름표 판정(`pet_member.group_actor_label`)에 쓰는 자리입니다 — 하루 요약은
    이미 계산한 것을 `DaySummary.group_ids` 로 받지만, 기록 응답과 409 본문은 그 계산을
    안 거쳐서 여기서 한 번 더 묻습니다.

    **권한을 다시 봅니다** — 라우터가 `pet_id` 를 그대로 넘기는 자리라, 여기서 안 보면
    남의 아이 id 로 그룹 구성을 떠볼 수 있습니다.
    """
    return await _group_ids(session, await _accessible_pet(session, app_user_id, pet_id))


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
    pet = await _accessible_pet(session, app_user_id, body.pet_id)

    existing = await care_repo.get_by_client_event(session, pet.id, body.client_event_id)
    if existing is not None:
        return existing, False

    # ⚠️ 이 검사는 멱등 조회보다 **뒤**여야 합니다. 앞으로 옮기면, 확인하고 기록한 직후의
    #    재시도가 방금 자기가 만든 행을 중복으로 보고 409 를 냅니다 — 앱은 올라갔는지
    #    모르게 됩니다 (test_idempotency_wins_over_conflict_check_even_without_explicit_confirm
    #    이 이것을 실제로 검증합니다 — `confirm=True` 인 왕복은 창 검사 자체를 건너뛰어
    #    순서를 구분하지 못합니다).
    #
    # 락은 걸지 않습니다 — 동시 기록은 **불변식이 아니라 경고**입니다. `care_events` 는 원래
    # 하루에 약 두 건을 허용하고(다른 약일 수 있다), 진짜 위험은 교대 경계의 몇십 분 차이지
    # 같은 순간의 동시 탭이 아닙니다 (docs/co-care.md §4 "일부러 안 하는 것").
    if body.kind in CONFIRM_KINDS and not body.confirm:
        window = MEDICATION_CONFIRM_WINDOW
        # **창을 그룹 전체로 넓힙니다** (MVP 결정 §7). 안 넓히면 A 가 자기 행에 적은 약을
        # B 의 행에서 못 봐서, 교대 경계의 중복 투약이 통째로 안 걸립니다 — 그것이 이
        # 확인의 존재 이유 전부입니다 (docs/co-care.md §4).
        conflicts = await care_repo.list_kind_between(
            session,
            await _group_ids(session, pet),
            body.kind,
            body.occurred_at - window,
            body.occurred_at + window,
        )
        if conflicts:
            raise MedicationConflictError(conflicts)

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
) -> tuple[list[CareEvent], datetime, datetime, list[uuid.UUID]]:
    """기간 조회. 창을 같이 돌려주는 이유는 기본값을 앱이 되짚어 볼 수 있게 하려는 것입니다."""
    pet = await _accessible_pet(session, app_user_id, pet_id)
    start, end = _window(start, end)
    group_ids = await _group_ids(session, pet)
    events = await care_repo.list_between(session, app_user_id, group_ids, start, end)
    return events, start, end, group_ids


async def delete_event(
    session: AsyncSession, app_user_id: uuid.UUID, event_id: uuid.UUID
) -> None:
    """지우기. **적은 사람 또는 그 아이의 대표만** 지웁니다 (docs/co-care.md §2).

    적은 사람은 **지금도 그 행의 구성원**이어야 합니다 (#574) — 나가거나 내보내진 뒤에는
    자기가 적은 줄이라도 404 입니다. 읽기와 같은 바닥이고, 기록 자체는 그대로 남습니다.

    돌봄 기록은 강아지 것이라(결정 ①) 대표는 돌보미의 오기록을 지울 수 있어야 하고,
    적은 사람은 자기 오기록을 지울 수 있어야 합니다. 반대로 **돌보미끼리는 못 지웁니다** —
    구성원 전체에 열면 아빠가 내가 적은 약 기록을 지우고, 그 사실이 아무 데도 안 남습니다.
    판정은 `care_repo.get_deletable` 이 쿼리 조건으로 들고 있습니다.
    """
    event = await care_repo.get_deletable(session, app_user_id, event_id)
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
    pet = await _accessible_pet(session, app_user_id, pet_id)
    day = day or datetime.now(ZoneInfo(timezone)).date()
    start, end = day_bounds(day, timezone)
    # 케어도 산책도 **논리 강아지 전체**를 셉니다 (MVP 결정 §7) — 연결된 두 행에 갈려
    # 쌓인 기록이 화면에서는 한 마리의 하루이기 때문입니다.
    group_ids = await _group_ids(session, pet)
    counts = await care_repo.count_by_kind(session, app_user_id, group_ids, start, end)
    events = await care_repo.list_between(session, app_user_id, group_ids, start, end)
    # 산책은 **행으로** 읽습니다 — 수만 세면 "누가 다녀왔는지" 를 못 보여 줍니다.
    # 수는 그 길이라, 세는 질의와 보여 주는 질의가 갈려 어긋날 자리가 없습니다.
    walk_rows = await walk_repo.list_for_pets_between(session, group_ids, start, end)
    return DaySummary(
        day=day, timezone=timezone, start=start, end=end,
        counts=counts, walks=len(walk_rows), events=events, walk_rows=walk_rows,
        group_ids=group_ids,
    )


__all__ = [
    "CONFIRM_KINDS",
    "DAY_TIMEZONE",
    "DEFAULT_RANGE",
    "MAX_RANGE",
    "MEDICATION_CONFIRM_WINDOW",
    "CareEventNotFoundError",
    "CareRangeError",
    "DaySummary",
    "MedicationConflictError",
    "day_bounds",
    "day_summary",
    "delete_event",
    "group_ids_for",
    "list_events",
    "record",
]
