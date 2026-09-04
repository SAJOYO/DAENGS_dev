"""운영 지표 HTTP 경계 (콘솔 로드맵 B3 · #223).

판단은 여기 없습니다. 기간을 자르고 무엇을 세는지는 `services/metrics.py` 가 정하고,
여기서는 그 결과를 스키마로 옮기며 **정렬만** 합니다 (많은 순).

**권한은 `metrics:read` 입니다.** `ROLE_PERMISSIONS` 기준으로 VIEWER 만 막히고
ADMIN · OPERATOR · CURATOR · ANALYST 는 통과합니다 — `ANALYST` 라는 role 이 존재하는
이유가 바로 이 화면입니다 ("지표·검색 점검 조회. 쓰기가 없습니다", `core/deps.py`).

**나가는 값에 원문이 없습니다** (D-037). 스키마에 담을 칸 자체를 안 만들었습니다 —
`schemas/metrics.py` 의 첫 문단.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import Perm, Principal, require
from daengs_backend.schemas.metrics import (
    CategoryMetricsOut,
    ChatMetricsOut,
    NamedCount,
    TurnMetricsOut,
)
from daengs_backend.services import metrics as metrics_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/metrics", tags=["admin-metrics"])


def _ranked(counts: dict[str, int]) -> list[NamedCount]:
    """많은 순으로. 같으면 이름 순이라 **새로고침해도 순서가 안 흔들립니다.**"""
    return [
        NamedCount(name=name, count=count)
        for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


@router.get("/chats", response_model=ChatMetricsOut)
async def chat_metrics(
    _admin: Annotated[Principal, Depends(require(Perm.METRICS_READ))],
    session: Annotated[AsyncSession, Depends(get_session)],
    days: Annotated[int, Query(ge=1, le=metrics_service.MAX_DAYS)] = (
        metrics_service.DEFAULT_DAYS
    ),
) -> ChatMetricsOut:
    """기간 안의 대화 숫자.

    **비율을 계산해서 주지 않습니다.** 분모가 하나가 아니기 때문입니다 — 능력별 분포는
    태그 수가, 응답 결과 분포는 `assistant_status` 가 있는 행이 분모입니다. 서버가 미리
    나누면 그 분모가 무엇이었는지가 사라집니다 (`services/metrics.py`).

    **응답에 `since` 와 `days` 를 실어 보냅니다.** 화면이 "왜 이 숫자냐" 를 말할 수
    있어야 하고, 기본값을 서버가 정하므로 클라이언트가 되짚어 계산하면 어긋납니다.
    """
    m = await metrics_service.collect_chat_metrics(session, days=days)

    return ChatMetricsOut(
        since=m.since,
        days=m.days,
        sessions=m.sessions,
        turns=TurnMetricsOut(
            total=m.turns_total,
            by_processing_status=_ranked(m.turns_by_processing_status),
            by_assistant_status=_ranked(m.turns_by_assistant_status),
            top_error_codes=[
                NamedCount(name=code, count=n) for code, n in m.top_error_codes
            ],
        ),
        categories=CategoryMetricsOut(
            counts=[NamedCount(name=cat, count=n) for cat, n in m.categories],
            tagged_total=m.tagged_total,
        ),
        summaries=_ranked(m.summaries_by_processing_status),
    )
