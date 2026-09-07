"""request_metrics 기록과 집계. 쿼리만 있고 판단은 없습니다.

"무엇을 남길 것인가"와 "언제 확정할 것인가"는 `services/request_metrics.py` 가 정합니다.

commit 은 하지 않습니다 — 다만 이 표에서는 **트랜잭션 경계가 다른 표와 반대입니다.**
보통은 services 가 다른 변경과 함께 한 번에 커밋하는데, 여기서는 지표 한 행이 사용자
응답과 **같은 트랜잭션에 있으면 안 됩니다.** 이유는 services 쪽 docstring 에 있습니다.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Row, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import RequestMetric


async def add(
    session: AsyncSession,
    *,
    request_id: uuid.UUID,
    principal_kind: str,
    status: str,
    elapsed_ms: int,
    router_kind: str | None = None,
    capabilities: Sequence[str] = (),
    reason_code: str | None = None,
    error_category: str | None = None,
) -> RequestMetric:
    """지표 한 행을 세션에 얹습니다.

    `capabilities` 가 비어 있는 것은 "빠뜨렸다"가 아니라 **아무 능력도 안 돌았다**는
    뜻입니다 — 사교적 응답과 라우터 실패가 그렇고, 그 둘을 0개로 남기는 것이 이 열의
    값입니다.

    `flush` 하지 않습니다. `admin_audit_log` 와 다른 점인데, 저쪽은 제약 위반을 그 자리에서
    드러내려고 flush 합니다. 여기는 **드러나면 안 됩니다** — 지표 한 행의 제약 위반이
    사용자 요청 흐름으로 새면 그것이 곧 "지표 때문에 답변이 죽는" 상태입니다.
    """
    entry = RequestMetric(
        request_id=request_id,
        principal_kind=principal_kind,
        router_kind=router_kind,
        capabilities=list(capabilities),
        status=status,
        reason_code=reason_code,
        error_category=error_category,
        elapsed_ms=elapsed_ms,
    )
    session.add(entry)
    return entry


async def latency_summary(session: AsyncSession, *, since: datetime) -> Row:
    """건수 · 평균 · 중앙값 · p95 · 최대. 기간 하나에 한 줄.

    **p95 를 `percentile_cont` 로 셉니다.** 정렬해서 자르는 것과 달리 한 번에 나오고,
    지금 규모에서는 인덱스 스캔 뒤 정렬이라 충분히 쌉니다. 이 질의가 느껴질 무렵이면
    그때가 보존 정책을 정할 때입니다 (`db/init/22_request_metrics.sql` 마지막 주석).

    평균만 보면 안 됩니다 — 느린 꼬리는 평균에 거의 안 잡힙니다. 그래서 p95 를 같이
    냅니다.
    """
    stmt = select(
        func.count(RequestMetric.id),
        func.avg(RequestMetric.elapsed_ms),
        func.percentile_cont(0.5).within_group(RequestMetric.elapsed_ms.asc()),
        func.percentile_cont(0.95).within_group(RequestMetric.elapsed_ms.asc()),
        func.max(RequestMetric.elapsed_ms),
    ).where(RequestMetric.created_at >= since)
    result = await session.execute(stmt)
    return result.one()


async def count_by(
    session: AsyncSession, *, column: str, since: datetime
) -> Sequence[Row]:
    """한 열의 값별 건수. 많은 순.

    **열 이름을 문자열로 받지만 임의의 값을 받지 않습니다** — 아래 화이트리스트에 없으면
    `KeyError` 입니다. 지금은 부르는 쪽이 상수뿐이라 필요 없어 보이지만, 이 함수가
    나중에 쿼리 파라미터를 받게 되는 날 이 줄이 SQL injection 을 막습니다.
    """
    allowed = {
        "status": RequestMetric.status,
        "principal_kind": RequestMetric.principal_kind,
        "router_kind": RequestMetric.router_kind,
        "reason_code": RequestMetric.reason_code,
        "error_category": RequestMetric.error_category,
    }
    target = allowed[column]

    stmt = (
        select(target, func.count(RequestMetric.id))
        .where(RequestMetric.created_at >= since)
        .group_by(target)
        .order_by(func.count(RequestMetric.id).desc())
    )
    result = await session.execute(stmt)
    return result.all()


async def count_by_capability(
    session: AsyncSession, *, since: datetime
) -> Sequence[Row]:
    """능력별 건수. **배열을 펼쳐서 셉니다** — 한 요청이 둘을 부르면 둘 다 셉니다.

    그래서 합계가 요청 수보다 클 수 있습니다. 그것이 맞습니다 — 여기서 묻는 것은
    "요청이 몇 건이냐"가 아니라 **"이 능력이 몇 번 불렸냐"** 입니다.

    아무 능력도 안 돈 요청(사교적 응답 · 라우터 실패)은 `unnest` 가 행을 안 내므로
    여기 안 나옵니다. 그 수는 `latency_summary` 의 건수와 견주면 나옵니다.
    """
    capability = func.unnest(RequestMetric.capabilities).label("capability")
    stmt = (
        select(capability, func.count())
        .where(RequestMetric.created_at >= since)
        .group_by(capability)
        .order_by(func.count().desc())
    )
    result = await session.execute(stmt)
    return result.all()
