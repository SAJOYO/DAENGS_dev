"""R — AI 도감 카드 쿼리. 판단은 `services/ai_card.py`·`services/ai_card_quota.py` 가 합니다."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

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
    """사용 기록 한 줄. 커밋은 부르는 쪽(ready 로 바꾸는 같은 트랜잭션)이 합니다."""
    session.add(usage)
    return usage


async def add_attempt_mark(
    session: AsyncSession, pick_group: uuid.UUID, app_user_id: uuid.UUID, *, marked_at: datetime
) -> None:
    """이 요청의 시도 표시(`unfulfilled_attempt = true`, `card_id = pick_group`)를 남깁니다 (D-084).

    **유료 호출 전에** 슬롯을 잡는 트랜잭션에서 부릅니다 — 그래야 호출 도중 카드를 지워도 표시가
    남습니다. 같은 `card_id` 줄이 이미 있으면 아무것도 안 합니다(`ON CONFLICT DO NOTHING`) — 표시가
    이미 있거나, 대표 행이 이미 좋은 카드가 되어 그 id(= `pick_group`)로 사용 기록이 있는 경우이고,
    둘 다 덮으면 안 됩니다. 커밋은 부르는 쪽이 합니다.
    """
    await session.execute(
        pg_insert(AiCardUsage)
        .values(card_id=pick_group, app_user_id=app_user_id, used_at=marked_at, unfulfilled_attempt=True)
        .on_conflict_do_nothing(index_elements=[AiCardUsage.card_id])
    )


async def count_usage_since(session: AsyncSession, app_user_id: uuid.UUID, since: datetime) -> int:
    """`since` 이후의 사용 기록 수. **지운 카드도 셉니다** — 기록은 카드와 따로 남습니다.

    **시도 표시(`unfulfilled_attempt`)는 세지 않습니다** — 그것은 하루 한도가 아니라 돈 나간 시도
    상한이 셉니다(`count_attempt_marks_since`, D-084).
    """
    stmt = select(func.count()).select_from(AiCardUsage).where(
        AiCardUsage.app_user_id == app_user_id,
        AiCardUsage.used_at >= since,
        AiCardUsage.unfulfilled_attempt.is_(False),
    )
    return int(await session.scalar(stmt) or 0)


async def count_attempt_marks_since(session: AsyncSession, app_user_id: uuid.UUID, since: datetime) -> int:
    """`since` 이후 **유료 호출까지 갔는데 좋은 카드가 안 나온(아직 안 나온) 요청** 수 — 요청마다 표시가
    한 줄뿐이라 행 수가 곧 요청 수다.

    카드 행(`ai_cards`)으로 세지 않는 이유: 카드는 지울 수 있다. 같은 강아지·같은 달을 다시 뽑으려면
    미달 카드를 지워야 하고(`has_month_card`), 생성 중에 지우면 행이 아예 안 남는다.
    """
    stmt = select(func.count()).select_from(AiCardUsage).where(
        AiCardUsage.app_user_id == app_user_id,
        AiCardUsage.used_at >= since,
        AiCardUsage.unfulfilled_attempt.is_(True),
    )
    return int(await session.scalar(stmt) or 0)


async def delete_attempt_mark(session: AsyncSession, pick_group: uuid.UUID) -> int:
    """이 요청의 시도 표시를 지웁니다 — 같은 요청에서 기준 이상 카드가 나와 사용 기록으로 바뀔 때.

    **표시(`unfulfilled_attempt = true`)만** 지웁니다. 대표 행의 사용 기록도 `card_id = pick_group` 이라
    조건이 없으면 그것까지 지울 수 있습니다. 커밋은 부르는 쪽(같은 트랜잭션)이 합니다.
    """
    result = await session.execute(
        sql_delete(AiCardUsage).where(AiCardUsage.card_id == pick_group, AiCardUsage.unfulfilled_attempt.is_(True))
    )
    return result.rowcount or 0


async def expire_generating(
    session: AsyncSession, app_user_id: uuid.UUID, *, stale_before: datetime, now: datetime
) -> int:
    """정리 기준보다 오래 **진척이 없는 요청**의 `generating` 을 `failed`/`interrupted` 로 바꿉니다.

    **진척은 행 하나가 아니라 요청(`pick_group`) 전체의 가장 최근 `updated_at` 입니다** (#572
    Task 5, D-084). 이 칸은 `services/ai_card.py` 가 슬롯을 잡을 때(`_claim_slot`)와 카드를 끝낼
    때(`_finish_ready`·`_finish_failed`) 찍습니다. 한 요청의 카드는 **하나씩 순서대로** 만들어지므로
    두 번째 카드의 `updated_at` 은 요청 시각 그대로이고, 첫 카드가 도는 동안에도 제 예산이
    흘러갑니다. 행마다 따로 재면 대기열이 길 때 첫 카드가 멀쩡히 도는 중에 두 번째가 `interrupted`
    로 덮여, 사용자가 돈 한 푼 안 나간 채 약속받은 카드 한 장을 조용히 잃었습니다.

    그래서 두 번째 카드는 **자기 그룹이 마지막으로 움직인 시각**부터 잽니다 — 첫 카드가 방금 슬롯을
    잡았거나 방금 끝났으면 살아 있고, 프로세스가 죽어 그룹 전체가 멈췄으면 그 마지막 진척에서
    `stale_after()` 가 지난 뒤 **그룹의 남은 행이 한꺼번에** 정리됩니다. 다른 요청의 진척은 보지
    않습니다. `pick_group` 이 없는 옛 행은 서브쿼리가 비어 제 `updated_at` 으로 잽니다.

    **여전히 이 예산 밖인 것:** 요청의 첫 카드가 세마포어(`cardimage_concurrency`)를 기다리는 시간.
    그 동안은 그룹의 어느 행도 안 움직입니다 — 그렇게 정리된 요청은 `_claim_slot` 이 엔진을 부르지
    않으므로 돈은 안 나갑니다. 커밋은 부르는 쪽이 합니다.
    """
    sibling = aliased(AiCard)
    last_progress = (
        select(func.max(sibling.updated_at))
        .where(sibling.app_user_id == app_user_id, sibling.pick_group == AiCard.pick_group)
        .scalar_subquery()
    )
    result = await session.execute(
        update(AiCard)
        .where(
            AiCard.app_user_id == app_user_id,
            AiCard.status == "generating",
            func.coalesce(last_progress, AiCard.updated_at) < stale_before,
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
