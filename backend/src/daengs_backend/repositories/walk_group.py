"""산책 기록 **공동 조회**용 읽기. 쿼리만 있고 권한 판단은 없습니다.

부르는 쪽(`services/walk_group.py`)이 요청자가 볼 수 있는 pet id 묶음을 먼저 정해서 넘깁니다.
여기서는 그 묶음에 태그된 산책만 읽습니다 — 소유자 조건을 걸지 않는 것이 이 파일의 뜻이라,
**쓰기 경로(`walk.get_owned`·`get_owned_for_update`)를 여기로 옮기면 안 됩니다.**

게임·점령 결과(`activity_walk_heads`·`activity_session_links`·`territory_claims`)는 읽지 않습니다.
거리·시간은 게임 설정과 무관하게 `walk_analyses` 최신 세대에서 읽습니다.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from daengs_backend.models import Walk, WalkAnalysis, WalkPet

__all__ = ["WalkSummaryNumbers", "get_in_group", "latest_numbers", "list_group_page"]


def _tagged_with(pet_ids: Sequence[uuid.UUID]):
    """그 묶음의 강아지가 하나라도 태그된 산책. 조인이 아니라 `IN` 서브쿼리라, 그룹의 두 아이가
    같은 산책에 태그돼도 산책 행이 **한 번만** 나옵니다 (`DISTINCT` 가 필요 없습니다)."""
    return Walk.id.in_(select(WalkPet.walk_id).where(WalkPet.pet_id.in_(set(pet_ids))))


async def list_group_page(
    session: AsyncSession,
    pet_ids: Sequence[uuid.UUID],
    *,
    limit: int,
    before: tuple[datetime, uuid.UUID] | None = None,
) -> list[Walk]:
    """최근 순 한 페이지. `before` 는 직전 페이지 마지막 산책의 `(started_at, id)` 입니다.

    `started_at` 이 같을 수 있어 `id` 로 한 번 더 정렬하고, 커서도 두 칸을 같이 비교합니다 —
    한 칸만 보면 같은 시각의 산책이 페이지 경계에서 빠지거나 두 번 나옵니다. 좌표는 안 붙입니다.
    """
    stmt = (
        select(Walk)
        .where(_tagged_with(pet_ids))
        .options(selectinload(Walk.pets))
        .order_by(Walk.started_at.desc(), Walk.id.desc())
        .limit(limit)
    )
    if before is not None:
        started_at, walk_id = before
        stmt = stmt.where(tuple_(Walk.started_at, Walk.id) < tuple_(started_at, walk_id))
    return list(await session.scalars(stmt))


async def get_in_group(
    session: AsyncSession, pet_ids: Sequence[uuid.UUID], walk_id: uuid.UUID
) -> Walk | None:
    """그 묶음의 강아지가 태그된 산책일 때만 좌표까지. **아니면 None** — 산책 id 만 알아서는
    남의 산책을 못 읽게 하는 자리입니다(IDOR)."""
    stmt = (
        select(Walk)
        .where(Walk.id == walk_id, _tagged_with(pet_ids))
        .options(selectinload(Walk.points), selectinload(Walk.pets))
    )
    return await session.scalar(stmt)


@dataclass(frozen=True)
class WalkSummaryNumbers:
    """목록·상세에 싣는 측정값 둘. 점수·기여도 같은 게임 값은 없습니다."""

    moving_distance_m: int
    moving_s: int


async def latest_numbers(
    session: AsyncSession, walk_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, WalkSummaryNumbers]:
    """산책마다 **가장 최근 계산 세대** 하나의 거리·이동 시간. 봉인 전이라 없으면 키가 없습니다.

    `walk_analyses` 는 한 산책에 세대가 여럿 쌓이는 표라 산책당 하나를 골라야 합니다
    (`DISTINCT ON (walk_id)`). `activity_walk_heads` 를 거치지 않는 이유는 그 표가
    `activity_game_enabled` 일 때만 생겨, 게임을 끈 환경에서 거리가 통째로 비기 때문입니다.
    """
    if not walk_ids:
        return {}
    stmt = (
        select(WalkAnalysis.walk_id, WalkAnalysis.moving_distance_m, WalkAnalysis.moving_s)
        .where(WalkAnalysis.walk_id.in_(set(walk_ids)))
        .order_by(WalkAnalysis.walk_id, WalkAnalysis.derived_at.desc(), WalkAnalysis.id.desc())
        .distinct(WalkAnalysis.walk_id)
    )
    rows = await session.execute(stmt)
    return {
        row.walk_id: WalkSummaryNumbers(int(row.moving_distance_m), int(row.moving_s))
        for row in rows
    }
