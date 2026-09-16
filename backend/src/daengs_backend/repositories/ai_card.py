"""R — AI 도감 카드 쿼리. 판단은 `services/ai_card.py`·`services/ai_card_quota.py` 가 합니다."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import AiCard, AiCardUsage


def add(session: AsyncSession, card: AiCard) -> AiCard:
    session.add(card)
    return card


async def get_owned(
    session: AsyncSession, app_user_id: uuid.UUID, card_id: uuid.UUID, *, for_update: bool = False
) -> AiCard | None:
    """**내 것일 때만** 돌려줍니다. 남의 것도 없는 것과 같은 None 입니다."""
    stmt = select(AiCard).where(AiCard.id == card_id, AiCard.app_user_id == app_user_id)
    if for_update:
        stmt = stmt.with_for_update()
    return await session.scalar(stmt)


async def get_for_update(session: AsyncSession, card_id: uuid.UUID) -> AiCard | None:
    """**소유자를 안 봅니다.** 백그라운드 생성이 자기가 만든 행을 다시 잡을 때만 씁니다."""
    return await session.scalar(select(AiCard).where(AiCard.id == card_id).with_for_update())


async def list_for_owner(session: AsyncSession, app_user_id: uuid.UUID, *, limit: int = 200) -> list[AiCard]:
    stmt = (
        select(AiCard)
        .where(AiCard.app_user_id == app_user_id)
        .order_by(AiCard.created_at.desc())
        .limit(limit)
    )
    return list(await session.scalars(stmt))


async def has_generating(session: AsyncSession, app_user_id: uuid.UUID) -> bool:
    stmt = select(AiCard.id).where(AiCard.app_user_id == app_user_id, AiCard.status == "generating").limit(1)
    return await session.scalar(stmt) is not None


async def has_month_card(session: AsyncSession, app_user_id: uuid.UUID, dog_id: uuid.UUID, month: int) -> bool:
    """이 보호자가 이 강아지로 이 달 카드를 이미 갖고 있나 (`ready`·`generating`). **실패는 안 봅니다.**

    보호자마다 따로 봅니다 — 같은 강아지라도 다른 보호자의 카드는 막지 않습니다 (D-077).
    """
    stmt = (
        select(AiCard.id)
        .where(
            AiCard.app_user_id == app_user_id,
            AiCard.dog_id == dog_id,
            AiCard.month == month,
            AiCard.status.in_(("generating", "ready")),
        )
        .limit(1)
    )
    return await session.scalar(stmt) is not None


def add_usage(session: AsyncSession, usage: AiCardUsage) -> AiCardUsage:
    """카드가 `ready` 가 된 기록. 커밋은 부르는 쪽(ready 로 바꾸는 같은 트랜잭션)이 합니다."""
    session.add(usage)
    return usage


async def count_usage_since(session: AsyncSession, app_user_id: uuid.UUID, since: datetime) -> int:
    """`since` 이후의 사용 기록 수. **지운 카드도 셉니다** — 기록은 카드와 따로 남습니다."""
    stmt = select(func.count()).select_from(AiCardUsage).where(
        AiCardUsage.app_user_id == app_user_id, AiCardUsage.used_at >= since
    )
    return int(await session.scalar(stmt) or 0)


async def count_failed_since(
    session: AsyncSession, app_user_id: uuid.UUID, since: datetime, codes: frozenset[str]
) -> int:
    """`since` 이후 만든 카드 중 `error_code` 가 `codes` 인 `failed` 수. 어떤 코드를 셀지는 한도가 정합니다."""
    stmt = select(func.count()).where(
        AiCard.app_user_id == app_user_id,
        AiCard.status == "failed",
        AiCard.error_code.in_(sorted(codes)),
        AiCard.created_at >= since,
    )
    return int(await session.scalar(stmt) or 0)


async def expire_generating(
    session: AsyncSession, app_user_id: uuid.UUID, *, stale_before: datetime, now: datetime
) -> int:
    """정리 기준보다 오래된 `generating` 을 `failed`/`interrupted` 로 바꿉니다.

    **`updated_at` 기준입니다**(`created_at` 이 아닙니다) — `services/ai_card.py::_claim_slot`
    이 슬롯을 잡을 때마다 이 칸을 지금으로 찍으므로, 대기열에서 오래 기다린 것이 아니라
    **슬롯을 잡고 실제로 도는 데** 걸린 시간만 이 기준에 들어갑니다. 배포 재시작과 겹쳐
    사라진 백그라운드 작업의 행이 여기 걸립니다. 커밋은 부르는 쪽이 합니다.
    """
    result = await session.execute(
        update(AiCard)
        .where(
            AiCard.app_user_id == app_user_id,
            AiCard.status == "generating",
            AiCard.updated_at < stale_before,
        )
        .values(status="failed", error_code="interrupted", updated_at=now)
    )
    return result.rowcount or 0


async def list_siblings(
    session: AsyncSession, app_user_id: uuid.UUID, pick_group: uuid.UUID, *, exclude_id: uuid.UUID
) -> list[AiCard]:
    """같은 요청(`pick_group`)에서 나온 **다른** 카드들. 「고른 카드만 남긴다」가 지울 대상을 찾을 때 쓴다.

    `for_update` 로 잠근다 — 지우는 동안 다른 조회가 끼어들지 않게.
    """
    stmt = (
        select(AiCard)
        .where(AiCard.app_user_id == app_user_id, AiCard.pick_group == pick_group, AiCard.id != exclude_id)
        .with_for_update()
    )
    return list(await session.scalars(stmt))


async def group_counts(session: AsyncSession, app_user_id: uuid.UUID, pick_group: uuid.UUID) -> tuple[int, int, int]:
    """`(total, ready, generating)` — 이 pick_group 에 **실제로 있는** 행 수·완료 수·진행중 수.

    `total` 은 설정값이 아니라 실제 행 수다 — seed 가 모자란 달은 요청보다 적은 장이 만들어질
    수 있다(#572 Task 4 fix round 1 Important 2). `generating` 이 0이면 이 요청은 더 나올 카드가
    없다는 뜻이다 — `services/ai_card.py::group_progress` 의 `finished` 가 이것을 쓴다.
    """
    stmt = select(
        func.count(),
        func.count().filter(AiCard.status == "ready"),
        func.count().filter(AiCard.status == "generating"),
    ).select_from(AiCard).where(AiCard.app_user_id == app_user_id, AiCard.pick_group == pick_group)
    row = (await session.execute(stmt)).one()
    return int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)


async def find_ready_by_storage_key(session: AsyncSession, storage_key: str) -> AiCard | None:
    """bridge 전용. ⚠️ 소유자 조건이 없습니다 — 대신 **backend 가 실제로 저장한 키인지**를 봅니다."""
    return await session.scalar(
        select(AiCard).where(AiCard.storage_key == storage_key, AiCard.status == "ready")
    )


async def list_for_owner_for_update(session: AsyncSession, app_user_id: uuid.UUID) -> list[AiCard]:
    stmt = select(AiCard).where(AiCard.app_user_id == app_user_id).with_for_update()
    return list(await session.scalars(stmt))


async def delete(session: AsyncSession, card: AiCard) -> None:
    await session.delete(card)


async def delete_all_for_owner(session: AsyncSession, app_user_id: uuid.UUID) -> int:
    """⚠️ `app_users` CASCADE 에 기대면 안 됩니다 — 탈퇴는 그 행을 남깁니다."""
    result = await session.execute(sql_delete(AiCard).where(AiCard.app_user_id == app_user_id))
    return result.rowcount or 0


async def delete_usage_for_owner(session: AsyncSession, app_user_id: uuid.UUID, *, before: datetime) -> int:
    """탈퇴 정리 — `before` **이전** 기록만 지웁니다. 어느 시각을 줄지는 서비스가 정합니다(KST 오늘 00:00).

    ⚠️ `app_users` CASCADE 에 기대면 안 됩니다 — 탈퇴는 그 행을 남깁니다.
    """
    result = await session.execute(
        sql_delete(AiCardUsage).where(AiCardUsage.app_user_id == app_user_id, AiCardUsage.used_at < before)
    )
    return result.rowcount or 0
