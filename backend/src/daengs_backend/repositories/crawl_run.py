"""crawl_runs 조회. 쿼리만 있고 판단은 없습니다.

"이 소스를 지금 돌려도 되나"는 services 가 봅니다. 여기서는 거르지 않고 있는 그대로
돌려줍니다 — 실패한 실행도 화면에 보여야 하고, `running` 으로 멈춰 있는 행은 **특히**
보여야 합니다 (워커가 죽었다는 뜻입니다).

commit 은 하지 않습니다. 이 표는 워커가 쓰고 앱은 읽기만 합니다.
"""

from collections.abc import Sequence

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models.crawl_run import CrawlRun


async def latest_per_source(session: AsyncSession) -> Sequence[CrawlRun]:
    """소스별 **가장 최근 실행 하나씩.** 관리자 화면의 기본 질의입니다.

    `DISTINCT ON` 은 PostgreSQL 전용이지만 이 프로젝트는 Postgres 고정이고(D-011),
    윈도우 함수로 쓰면 서브쿼리 한 겹이 더 생깁니다. 인덱스
    `idx_crawl_runs_source_started (source_id, started_at DESC)` 가 이 질의를 위해 있습니다.
    """
    stmt = (
        select(CrawlRun)
        .distinct(CrawlRun.source_id)
        .order_by(CrawlRun.source_id, desc(CrawlRun.started_at))
    )
    return (await session.scalars(stmt)).all()


async def list_for_source(session: AsyncSession, source_id: str,
                          limit: int = 20) -> Sequence[CrawlRun]:
    """한 소스의 실행 이력. 최신순입니다."""
    stmt = (
        select(CrawlRun)
        .where(CrawlRun.source_id == source_id)
        .order_by(desc(CrawlRun.started_at))
        .limit(limit)
    )
    return (await session.scalars(stmt)).all()


async def list_by_run_id(session: AsyncSession, run_id: str) -> Sequence[CrawlRun]:
    """한 번의 수집이 무엇을 돌았나. `crawl_log.jsonl` 로 내려가기 전의 마지막 자리입니다."""
    stmt = select(CrawlRun).where(CrawlRun.run_id == run_id).order_by(CrawlRun.id)
    return (await session.scalars(stmt)).all()


async def count_running(session: AsyncSession) -> int:
    """아직 안 끝난 실행 수.

    **0 이 아니면 둘 중 하나입니다** — 지금 돌고 있거나, 워커가 죽어서 남았거나.
    둘을 구분하는 것은 이 표가 아니라 워커 상태이므로, 화면에서는 `started_at` 을 같이
    보여 주고 사람이 판단하게 둡니다.
    """
    stmt = select(func.count()).select_from(CrawlRun).where(CrawlRun.status == "running")
    return await session.scalar(stmt) or 0
