"""활성 반려견 → 비서가 받아도 되는 최근 진료비 요약 (#353 Task 7).

`services/care_log_context.py` 와 같은 층이고 같은 규칙입니다 — **소유권을 확인해 읽고,
좁혀서 넘기고, 못 채우면 조용히 None, `SQLAlchemyError` 삼킴.** 넘어가는 모양의 정의는
`orchestration.contracts` 의 `VetSpendContext` 이고, 이 파일은 그 모양을
`repositories.vet_visit` 의 `list_between` · `sum_by_reason` 에서 만듭니다.

**`by_reason_12m` 의 키는 표시명이다, 코드가 아니다.** 한 프롬프트 안에서
`last_visit.reason` 은 "피부" 인데 사유별 누계가 `skin` 으로 찍히면, 모델에게 같은 것의
이름이 둘로 보이고 그것이 한국어로 답할 때 "skin" 이라는 말을 끌어낼 자리가 됩니다.
`VET_REASON_LABELS` 를 양쪽에 같이 씁니다 (docs/vet-visits.md "채팅이 받는 것").

**응급·종양 집계는 아직 안 보냅니다.** 승인된 범위는 마지막 방문 + 이번 달 합계 + 사유별
누계뿐입니다. `is_emergency`/`is_oncology` 집계는 문서의 "열린 것" 에 남겨 둡니다 — 나중에
필요해지면 그때 이 칸을 넓힙니다.

**안 넘어가는 것들** — `reason_detail`(유저 자유 텍스트), `raw_ocr_items`,
`hospital_address`, 영수증 사진. 앞의 둘은 `#344` 가 `note` 를 뺀 것과 같은 이유이고,
`hospital_address` 는 프롬프트에서 할 일이 없습니다 — 이름·전화만 갑니다.

**기록이 하나도 없으면 None 입니다.** 빈 진료비 기록은 "아직 병원에 안 갔다" 가 아니라
"이 기능을 안 쓴다" 일 수 있습니다 — 케어 로그와 같은 이유로, 어느 쪽인지 모르는 채로
빈 블록을 실으면 모델이 둘 중 하나로 잘못 읽습니다.

**DB 오류도 None 입니다.** `vet_visits` 표는 이 기능의 마이그레이션이 서버 DB 에 적용돼야
생기고, 그것은 배포 담당자의 일이라 이 코드가 먼저 배포될 수 있습니다. 그때 조회는
`ProgrammingError` 를 내는데, 로그를 못 읽었다고 답할 수 있는 질문을 실패시키지 않습니다 —
경고 한 줄을 남기고 로그 없이 답합니다.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import VET_REASON_LABELS
from daengs_backend.repositories import pet as pet_repo
from daengs_backend.repositories import vet_visit as vet_repo

log = logging.getLogger(__name__)

__all__ = ["resolve"]

#: 사유별 누계를 세는 창. "피부로 1년간 얼마 썼나" 가 실제 질문입니다 (docs 머리말).
_BY_REASON_WINDOW = timedelta(days=366)
#: 최근 방문 건수를 세는 창.
_RECENT_WINDOW = timedelta(days=30)


async def resolve(
    session: AsyncSession,
    app_user_id: uuid.UUID,
    active_dog_id: str,
    *,
    today: date | None = None,
) -> dict[str, object] | None:
    """`context["vet_spend"]` 에 넣을 값. **없으면 None 이고, 그것은 오류가 아닙니다.**

    id 가 UUID 가 아니거나 남의 강아지면 조용히 None 입니다 — 소유권은
    `pet_repo.get_owned` 가 쿼리 조건으로 묶고 있어 남의 id 로는 못 읽습니다. `today` 는
    테스트가 시계를 고정하는 자리이고, 안 주면 오늘입니다.
    """
    try:
        pet_id = uuid.UUID(active_dog_id)
    except (ValueError, AttributeError, TypeError):
        return None
    today = today or datetime.now(UTC).date()

    try:
        pet = await pet_repo.get_owned(session, app_user_id, pet_id)
        if pet is None:
            return None
        visits = await vet_repo.list_between(session, app_user_id, pet_id, date.min, today)
        if not visits:
            return None
        by_reason = await vet_repo.sum_by_reason(
            session, app_user_id, pet_id, today - _BY_REASON_WINDOW, today
        )
    except SQLAlchemyError as exc:
        # 표가 아직 없거나 DB 가 잠깐 아플 때. 비서는 로그 없이 답합니다.
        log.warning("vet_visits 요약을 못 읽어 로그 없이 답합니다: %s", exc)
        return None

    month_start = today.replace(day=1)
    recent_start = today - _RECENT_WINDOW
    month_total = sum(visit.total_krw for visit in visits if visit.visited_on >= month_start)
    visit_count_30d = sum(1 for visit in visits if visit.visited_on >= recent_start)
    last = visits[0]  # list_between 이 최근 먼저로 준다

    last_visit: dict[str, object] = {
        "date": last.visited_on.isoformat(),
        "reason": VET_REASON_LABELS.get(last.reason_code, last.reason_code),
        "total_krw": last.total_krw,
    }
    if last.hospital_name:
        last_visit["hospital"] = last.hospital_name
    if last.hospital_phone:
        last_visit["phone"] = last.hospital_phone

    resolved: dict[str, object] = {
        "month_total_krw": month_total,
        "visit_count_30d": visit_count_30d,
        "last_visit": last_visit,
    }
    if by_reason:
        resolved["by_reason_12m"] = {
            VET_REASON_LABELS.get(code, code): total for code, total in by_reason.items()
        }
    return resolved
