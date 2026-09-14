"""care_events 조회·저장. 쿼리만 있고 판단은 없습니다 (#332).

조회 셋이 `pet_id` 하나가 아니라 **`pet_ids` 묶음**을 받습니다 (MVP 결정 §7). 같은 실제
강아지를 두 사람이 각자 등록해 연결하면 기록이 두 `pet_id` 에 갈려 쌓이는데, 화면에서는
한 마리이므로 읽을 때 합쳐야 합니다. **묶음을 만드는 것은 서비스**이고
(`services/pet_identity.py::group_pet_ids_of`), 앱이 보낸 id 목록을 그대로 받는 자리는
어디에도 없습니다 — 그래야 IDOR 이 안 생깁니다.

연결이 없으면 묶음이 한 마리라 쿼리 모양만 `IN` 으로 바뀝니다.
`idx_care_events_pet_occurred (pet_id, occurred_at DESC)` 를 그대로 탑니다.

"기간이 너무 넓은가"·"이 강아지에 닿을 수 있는가" 는 services 가 정합니다. commit 도 하지
않습니다 — 트랜잭션 경계는 services 가 잡습니다.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import CareEvent, Pet

__all__ = [
    "add",
    "count_by_kind",
    "delete",
    "get_by_client_event",
    "get_deletable",
    "list_between",
    "list_kind_between",
]


def add(session: AsyncSession, event: CareEvent) -> CareEvent:
    session.add(event)
    return event


async def get_deletable(
    session: AsyncSession, app_user_id: uuid.UUID, event_id: uuid.UUID
) -> CareEvent | None:
    """**적은 사람 또는 그 아이의 대표**일 때만 돌려줍니다 (docs/co-care.md §2).

    이름이 `get_owned` 가 아닌 이유는 **소유자가 둘이 아니기 때문**입니다. 기록의 주인은
    강아지고(결정 ①), 지울 자격이 있는 사람이 둘일 뿐입니다 — `actor_app_user_id` 는
    "챙긴 사람" 이지 소유자가 아닙니다. actor 만으로 거르면 대표가 돌보미의 오기록을
    영영 못 지우고, 반대로 구성원 전체에 열면 돌보미끼리 서로의 기록을 지웁니다.

    **조건을 쿼리 안에 둡니다** — 행을 먼저 꺼내 놓고 밖에서 사람을 비교하면, 부르는 쪽이
    그 비교를 잊는 자리가 생깁니다 (`pet_repo.get_owned` 와 같은 이유).
    """
    stmt = (
        select(CareEvent)
        .join(Pet, Pet.id == CareEvent.pet_id)
        .where(
            CareEvent.id == event_id,
            or_(
                CareEvent.actor_app_user_id == app_user_id,
                Pet.app_user_id == app_user_id,
            ),
        )
    )
    return await session.scalar(stmt)


async def get_by_client_event(
    session: AsyncSession, pet_id: uuid.UUID, client_event_id: uuid.UUID
) -> CareEvent | None:
    """멱등키로 찾습니다. UNIQUE (pet_id, client_event_id) 가 있어 많아야 한 건입니다."""
    stmt = select(CareEvent).where(
        CareEvent.pet_id == pet_id, CareEvent.client_event_id == client_event_id
    )
    return await session.scalar(stmt)


async def list_between(
    session: AsyncSession,
    _app_user_id: uuid.UUID,
    pet_ids: Sequence[uuid.UUID],
    start: datetime,
    end: datetime,
) -> list[CareEvent]:
    """`occurred_at` 이 `[start, end)` 인 것, **최근 먼저.** 같은 시각이면 `id` 로 한 번 더 정렬합니다.

    **actor 로 거르지 않습니다** (docs/co-care.md §2). 부르는 쪽(`care_event` 서비스)이 이미
    강아지 접근 권한을 확인한 뒤이고, 여기서 다시 사람으로 거르면 **다른 보호자가 적은 줄만
    빠집니다** — 그러면 두 사람이 같은 밥을 두 번 줍니다. 누가 챙겼는지는 `actor_app_user_id`
    에 남아 응답이 보여 줍니다. 인자는 부르는 쪽을 안 고치려고 시그니처에만 남겨 둡니다.
    """
    stmt = (
        select(CareEvent)
        .where(
            CareEvent.pet_id.in_(set(pet_ids)),
            CareEvent.occurred_at >= start,
            CareEvent.occurred_at < end,
        )
        .order_by(CareEvent.occurred_at.desc(), CareEvent.id)
    )
    return list(await session.scalars(stmt))


async def list_kind_between(
    session: AsyncSession,
    pet_ids: Sequence[uuid.UUID],
    kind: str,
    start: datetime,
    end: datetime,
) -> list[CareEvent]:
    """그 아이의 같은 종류 기록, 창 안에서 최근 먼저. 약 중복 확인이 씁니다 (docs/co-care.md §4).

    **양끝을 포함합니다** (`<=`) — 창이 `occurred_at ± 6시간` 으로 대칭이라, `list_between` 의
    `[start, end)` 반열림과 달리 여기서는 끝점도 부딪혀야 합니다.
    """
    stmt = (
        select(CareEvent)
        .where(
            CareEvent.pet_id.in_(set(pet_ids)),
            CareEvent.kind == kind,
            CareEvent.occurred_at >= start,
            CareEvent.occurred_at <= end,
        )
        .order_by(CareEvent.occurred_at.desc())
    )
    return list(await session.scalars(stmt))


async def count_by_kind(
    session: AsyncSession,
    _app_user_id: uuid.UUID,
    pet_ids: Sequence[uuid.UUID],
    start: datetime,
    end: datetime,
) -> dict[str, int]:
    """종류별 건수. **없는 종류는 키가 없습니다** — 부르는 쪽이 `.get(kind, 0)` 을 씁니다.

    `list_between` 과 같게 **actor 로 거르지 않습니다** — 하루 요약의 "밥 2" 는 그 아이가
    두 번 먹었다는 뜻이지 내가 두 번 줬다는 뜻이 아닙니다 (docs/co-care.md §2).
    """
    stmt = (
        select(CareEvent.kind, func.count())
        .where(
            CareEvent.pet_id.in_(set(pet_ids)),
            CareEvent.occurred_at >= start,
            CareEvent.occurred_at < end,
        )
        .group_by(CareEvent.kind)
    )
    return {kind: int(n) for kind, n in (await session.execute(stmt)).all()}


async def delete(session: AsyncSession, event: CareEvent) -> None:
    await session.delete(event)
