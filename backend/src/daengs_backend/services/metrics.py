"""운영 지표 집계 (콘솔 로드맵 B3 · #223).

HTTP 를 모릅니다 — 기간을 해석하고 리포지토리의 여섯 쿼리를 모아 dataclass 로 냅니다.

**여기에 비율 계산이 없습니다.** 나누는 것은 화면이 합니다. 이유는 분모가 하나가 아니기
때문입니다 — 능력별 분포의 분모는 turn 수가 아니라 태그 수이고(`agent_categories` 가
배열입니다), 응답 결과 분포의 분모는 `assistant_status` 가 있는 행뿐입니다. 서버가
비율을 미리 계산해 보내면 **그 분모가 무엇이었는지가 사라집니다.** 개수와 분모를 같이
보내고 나누는 것은 그리는 쪽에 맡깁니다.

**0건일 때가 유일하게 실제로 밟히는 경로입니다** (2026-09-04 기준 개발 DB 의 `chat_turns`
가 0건). 그래서 이 파일에는 나눗셈이 없고, 화면 쪽에 0 방어가 있습니다.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.repositories import chat_metrics as repo

logger = logging.getLogger(__name__)

__all__ = ["DEFAULT_DAYS", "MAX_DAYS", "ChatMetrics", "collect_chat_metrics"]

#: 기본 기간. 화면이 "최근 30일" 이라고 적을 수 있게 서버가 정합니다.
DEFAULT_DAYS = 30
#: 위쪽 한계. 전체 기간을 기본으로 두면 시간이 갈수록 느려집니다
#: (`chat_turns` 에 `created_at` 단독 인덱스가 없습니다).
MAX_DAYS = 365


@dataclass(frozen=True)
class ChatMetrics:
    since: datetime
    days: int
    sessions: int
    turns_total: int
    turns_by_processing_status: dict[str, int]
    turns_by_assistant_status: dict[str, int]
    top_error_codes: list[tuple[str, int]]
    categories: list[tuple[str, int]]
    summaries_by_processing_status: dict[str, int]

    @property
    def tagged_total(self) -> int:
        """능력 태그의 총수. **turn 수가 아닙니다** — 배열이라 하나가 여럿을 답니다."""
        return sum(count for _, count in self.categories)


async def collect_chat_metrics(
    session: AsyncSession, *, days: int = DEFAULT_DAYS
) -> ChatMetrics:
    """기간 안의 대화 숫자를 모읍니다.

    **쿼리를 순서대로 부릅니다.** 한 세션을 공유하므로 병렬로 돌릴 수 없고(asyncpg 연결
    하나에 동시 실행을 하면 `InterfaceError`), 지금 행 수로는 그럴 이유도 없습니다.

    `turns_total` 은 `by_processing_status` 의 합입니다 — 따로 세면 두 쿼리 사이에 행이
    늘어 **합이 안 맞는 화면**이 나옵니다. 한 번 세서 나눠 담는 편이 언제나 일관됩니다.
    """
    capped = max(1, min(days, MAX_DAYS))
    since = datetime.now(UTC) - timedelta(days=capped)

    by_processing = await repo.count_turns_by_processing_status(session, since=since)
    metrics = ChatMetrics(
        since=since,
        days=capped,
        sessions=await repo.count_sessions(session, since=since),
        turns_total=sum(by_processing.values()),
        turns_by_processing_status=by_processing,
        turns_by_assistant_status=await repo.count_turns_by_assistant_status(
            session, since=since
        ),
        top_error_codes=await repo.count_error_codes(session, since=since),
        categories=await repo.count_agent_categories(session, since=since),
        summaries_by_processing_status=await repo.count_summaries_by_processing_status(
            session, since=since
        ),
    )
    logger.info(
        "대화 지표 집계 (%d일, 세션 %d, turn %d)",
        capped,
        metrics.sessions,
        metrics.turns_total,
    )
    return metrics
