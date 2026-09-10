"""crawl_runs 조회. 쿼리만 있고 판단은 없습니다.

"이 소스를 지금 돌려도 되나"는 services 가 봅니다. 여기서는 거르지 않고 있는 그대로
돌려줍니다 — 실패한 실행도 화면에 보여야 하고, `running` 으로 멈춰 있는 행은 **특히**
보여야 합니다 (워커가 죽었다는 뜻입니다).

commit 은 하지 않습니다. 이 표는 워커가 쓰고 앱은 읽기만 합니다.
"""

from collections.abc import Sequence
from datetime import datetime

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


async def count_running(session: AsyncSession, *,
                        started_before: datetime | None = None) -> int:
    """아직 안 끝난 실행 수. `started_before` 를 주면 **그 시각 이전에 시작한 것만** 셉니다.

    **0 이 아니면 둘 중 하나입니다** — 지금 돌고 있거나, 워커가 죽어서 남았거나.
    둘을 가르는 것은 `started_at` 이 얼마나 오래됐는가인데, **얼마나가 오래인지는 여기서
    정하지 않습니다** (이 파일은 쿼리만 있고 판단은 없습니다 — 파일 머리). 자르는 시각은
    services 가 계산해서 인자로 넘깁니다 (`services/crawl.py` 의 `RUNNING_STALE_AFTER`).

    ⚠ **잔존 행을 거르라는 뜻이 아닙니다.** 인자를 안 주면 예전처럼 전부 셉니다 —
    `CrawlStatusOut.running` 이 그 값을 씁니다.
    """
    stmt = select(func.count()).select_from(CrawlRun).where(CrawlRun.status == "running")
    if started_before is not None:
        stmt = stmt.where(CrawlRun.started_at < started_before)
    return await session.scalar(stmt) or 0
