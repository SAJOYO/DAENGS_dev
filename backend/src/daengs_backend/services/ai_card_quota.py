"""앱 사용자 AI 카드 생성 한도 (#537, D-076).

**지금 규칙은 테스트 단계용입니다** — 사용자별 동시 1장 + KST 하루 `ready` N장
(`DAENGS_CARDIMAGE_DAILY_LIMIT`, 기본 1). 카드를 몇 장·어떤 조건으로 줄지(제품 규칙)가 정해지면
**`check_quota` 를 통째로 바꿉니다.** 부르는 쪽(`services/ai_card.py`)은 두 예외만 압니다.

실패(`failed`)는 세지 않습니다 — 한도가 1장이라 실패 한 번으로 그날 기회가 사라지면 안 되고,
연타는 동시 1장이 막습니다. 전체 지출의 바닥은 카드 생성 키의 별도 GCP 프로젝트 지출 상한입니다.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.config import settings
from daengs_backend.repositories import ai_card as ai_card_repo

KST = ZoneInfo("Asia/Seoul")


class AiCardBusyError(Exception):
    """이미 만들고 있는 카드가 있습니다. 라우터가 409 `already_generating` 으로 바꿉니다."""


class AiCardLimitError(Exception):
    """오늘 한도를 다 썼습니다. 라우터가 429 `limit_reached` 로 바꿉니다."""


def stale_after() -> timedelta:
    """`generating` 을 사라진 작업으로 볼 기준. **슬롯을 잡은 시각(`updated_at`)부터** 잰다.

    한 건의 최악은 슬롯을 잡은 뒤 엔진·검수가 각각 `cardimage_timeout_ms` 를 다 쓰고
    재시도까지 하는 경우(2 × 2 × timeout)다. 그보다 짧으면 정상 진행 중인 작업을 실패로
    덮으므로 1분을 더 둔다. **세마포어를 기다리는 대기열 시간은 이 예산 밖이다** —
    `services/ai_card.py::_claim_slot` 이 슬롯을 잡고 돈이 나가는 호출(엔진) 직전에 행을
    다시 보아 `updated_at` 을 그 시각으로 찍으므로, 대기가 길어져도 정리 기준이 그동안
    부풀지 않고, 대기 중에 지워지거나 이미 정리된 행은 애초에 엔진을 부르지 않는다.
    """
    return timedelta(milliseconds=4 * settings.cardimage_timeout_ms) + timedelta(seconds=60)


def kst_day_start(now: datetime) -> datetime:
    return now.astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)


async def check_quota(
    session: AsyncSession, app_user_id: uuid.UUID, *, now: datetime, daily_limit: int
) -> None:
    await ai_card_repo.expire_generating(
        session, app_user_id, stale_before=now - stale_after(), now=now
    )
    if await ai_card_repo.has_generating(session, app_user_id):
        raise AiCardBusyError
    if daily_limit and await ai_card_repo.count_ready_since(
        session, app_user_id, kst_day_start(now)
    ) >= daily_limit:
        raise AiCardLimitError
