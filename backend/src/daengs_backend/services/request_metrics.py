"""요청 하나가 남기는 것을 적습니다 (D-037 · 콘솔 로드맵 B2).

--------------------------------------------------------------------------------
**이 파일에서 제일 중요한 규칙: 지표가 답변을 죽이면 안 됩니다.**

지표는 곁다리입니다. 한 행을 못 남긴 것 때문에 사용자가 500 을 받으면 본말전도이고,
그런 일은 실제로 잘 일어납니다 — 새 컬럼에 CHECK 를 걸었는데 코드가 아직 옛 값을
보내는 배포 중간 상태 같은 자리입니다. 그래서 여기서는 **모든 예외를 삼킵니다.**

삼키는 대가로 **조용히 안 남는 상태**가 생깁니다. 그것을 알아채라고 `logger.warning`
을 답니다 — WARNING 은 로깅 설정이 없어도 stderr 로 나갑니다(루트 로거 기본값이
WARNING 이라 `logger.info` 는 통째로 버려집니다. Hold 상태인 로그 카드가 그 문제를
소유합니다). **`info` 로 낮추지 마세요** — 낮추는 순간 아무 데도 안 남습니다.

**세션도 따로 씁니다.** 요청 수명의 `get_session` 에 얹으면 두 방향으로 샙니다:
  ⓐ 요청이 롤백되면 지표도 같이 사라진다 — 실패한 요청이야말로 남아야 하는 것이다
  ⓑ 지표 쪽 제약 위반이 그 세션을 오염시켜 사용자 응답의 커밋까지 죽인다
대화 저장(`chat_service`)이 같은 이유로 이미 자기 공장을 받고 있어서, 그 배선을
그대로 씁니다 (`core/database.get_chat_session_factory`).
--------------------------------------------------------------------------------
"""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from daengs_backend.orchestration.contracts import AssistantResponse
from daengs_backend.repositories import request_metrics as metrics_repo

logger = logging.getLogger(__name__)

__all__ = [
    "LatencySummary",
    "RequestMetricsReport",
    "measured",
    "report",
]


async def _write(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    request_id: uuid.UUID,
    principal_kind: str,
    status: str,
    elapsed_ms: int,
    router_kind: str | None = None,
    capabilities: Sequence[str] = (),
    reason_code: str | None = None,
    error_category: str | None = None,
) -> None:
    """짧은 TX 하나. **실패해도 안 던집니다.**"""
    try:
        async with session_factory() as session:
            await metrics_repo.add(
                session,
                request_id=request_id,
                principal_kind=principal_kind,
                status=status,
                elapsed_ms=elapsed_ms,
                router_kind=router_kind,
                capabilities=capabilities,
                reason_code=reason_code,
                error_category=error_category,
            )
            await session.commit()
    except Exception:  # noqa: BLE001 — 넓게 잡는 것이 이 함수의 목적입니다 (머리말)
        # 좁히면 새 예외 종류가 하나 생길 때마다 사용자 응답이 죽습니다. 여기서 잡는
        # 것들이 무엇인지 미리 셀 수 없다는 것이 정확히 이 표의 성질입니다.
        #
        # WARNING 이라야 로깅 설정 없이도 stderr 로 나갑니다 — 머리말 참고.
        logger.warning("요청 지표를 남기지 못했습니다 (request_id=%s)", request_id)


def _reason_code_of(response: AssistantResponse) -> str | None:
    """거절 · 기권의 **코드**. `OutcomeDetail.code` 이고 `message` 가 아닙니다.

    문구를 적으면 안 됩니다 — 문구는 사용자에게 보이는 말이라 다듬을 때마다 바뀌고,
    바뀌면 집계가 거기서 끊깁니다. `/life/ask` 가 스스로 이름 붙인 결과
    (`no_evidence` · `*_boundary`, #177)가 그 코드로 올라옵니다.

    **여러 능력이 돌면 첫 번째 것만 적습니다.** 사유가 둘인 요청은 드물고, 이어 붙이면
    집계에서 새 값이 무한히 생깁니다.

    기권과 거절 중 **기권을 먼저 봅니다** — 한 결과가 둘 다 가질 수는 없지만
    (`status_has_matching_metadata`), 순서를 정해 두지 않으면 나중에 그 불변식이
    느슨해질 때 값이 조용히 흔들립니다.
    """
    for result in response.results:
        detail = result.abstention or result.refusal
        if detail is not None:
            return detail.code[:60]
    return None


async def measured(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    principal_kind: str,
    run: Callable[[], Awaitable[AssistantResponse]],
    ignore: tuple[type[BaseException], ...] = (),
) -> AssistantResponse:
    """`run()` 을 재고, 결과를 한 행으로 남기고, 결과를 그대로 돌려줍니다.

    **`run()` 의 예외를 바꾸지 않습니다.** `ignore` 에 든 것은 그냥 다시 던지고,
    그 밖의 예외는 `error_category` 로 한 행 남기고 **그대로 다시 던집니다** — 여기서
    삼키면 500 이 200 이 됩니다.

    **`ignore` 를 부르는 쪽이 정합니다.** 이 모듈이 `HTTPException` 을 알면 services 가
    프레임워크를 물게 되는데, 이 저장소의 `services/` 38개 중 fastapi 를 import 하는 것은
    하나도 없습니다. 그리고 **"무엇이 계약된 클라이언트 오류인가" 는 HTTP 경계의 일**입니다
    — `routers/assistant.py` 가 `include_route_trace` 를 자기가 정하는 것과 같은 이유입니다
    (권한이 그쪽 소유이듯 상태 코드도 그쪽 소유입니다).

    지연은 `perf_counter` 로 잽니다. 벽시계(`datetime.now`)로 재면 NTP 보정이 들어올 때
    음수가 나오고, 그런 행이 섞이면 평균이 조용히 틀립니다.
    """
    started = time.perf_counter()
    try:
        response = await run()
    except ignore:
        # 부르는 쪽이 "이건 오케스트레이션 지표가 아니다" 라고 알려 준 것들입니다.
        raise
    except Exception as exc:
        await _write(
            session_factory,
            request_id=uuid.uuid4(),
            principal_kind=principal_kind,
            status="FAILED",
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            # **타입 이름까지입니다.** 메시지에는 질문이나 좌표가 섞여 들어옵니다.
            error_category=type(exc).__name__[:60],
        )
        raise

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    try:
        request_id = uuid.UUID(response.request_id)
    except (ValueError, AttributeError, TypeError):
        # `request_id` 가 UUID 가 아니면 그 요청은 못 남깁니다 — 컬럼이 uuid 라서입니다.
        # 새 값을 만들어 넣지 않습니다. 아무것도 안 가리키는 id 는 있는 것보다 나쁩니다.
        logger.warning("request_id 가 UUID 가 아니라 지표를 안 남깁니다: %r", response.request_id)
        return response

    await _write(
        session_factory,
        request_id=request_id,
        principal_kind=principal_kind,
        status=str(response.status),
        elapsed_ms=elapsed_ms,
        router_kind=str(response.route.router) if response.route else None,
        # **빈 배열이 정상입니다** — 사교적 응답과 라우터 실패는 아무 능력도 안 부릅니다.
        capabilities=[str(r.capability) for r in response.results],
        reason_code=_reason_code_of(response),
    )
    return response


# ── 읽는 쪽 ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LatencySummary:
    """지연 한 줄. **평균만 보면 안 됩니다** — 느린 꼬리는 평균에 거의 안 잡힙니다."""

    total: int
    avg_ms: int | None
    p50_ms: int | None
    p95_ms: int | None
    max_ms: int | None


@dataclass(frozen=True)
class RequestMetricsReport:
    """`/console/metrics` 가 그리는 것. **원문 없이 숫자만입니다** (D-037)."""

    days: int
    since: datetime
    latency: LatencySummary
    by_status: list[tuple[str, int]]
    by_capability: list[tuple[str, int]]
    by_reason_code: list[tuple[str, int]]
    by_router_kind: list[tuple[str, int]]


def _rows(rows) -> list[tuple[str, int]]:  # type: ignore[no-untyped-def]
    """`(값, 건수)` 로 펴고 NULL 은 버립니다.

    NULL 을 `"(없음)"` 같은 말로 바꾸지 않습니다 — 화면이 그 말을 값으로 오해합니다.
    reason_code 가 NULL 인 요청은 "사유가 없다"가 아니라 **거절이 아니었다**는 뜻이고,
    그 수는 전체 건수에서 빼면 나옵니다.
    """
    return [(str(value), int(count)) for value, count in rows if value is not None]


async def report(
    session: AsyncSession, *, days: int
) -> RequestMetricsReport:
    """기간 하나의 집계. 기간 전환(7 · 30 · 90일)은 화면이 고릅니다.

    `days` 는 라우터가 이미 좁혀서 넘깁니다 — 여기서 다시 막지 않는 것은 경계가
    한 곳이어야 해서입니다.
    """
    since = datetime.now(UTC) - timedelta(days=days)

    total, avg, p50, p95, maximum = await metrics_repo.latency_summary(
        session, since=since
    )
    latency = LatencySummary(
        total=int(total or 0),
        avg_ms=int(avg) if avg is not None else None,
        p50_ms=int(p50) if p50 is not None else None,
        p95_ms=int(p95) if p95 is not None else None,
        max_ms=int(maximum) if maximum is not None else None,
    )

    return RequestMetricsReport(
        days=days,
        since=since,
        latency=latency,
        by_status=_rows(await metrics_repo.count_by(session, column="status", since=since)),
        by_capability=_rows(
            await metrics_repo.count_by_capability(session, since=since)
        ),
        by_reason_code=_rows(
            await metrics_repo.count_by(session, column="reason_code", since=since)
        ),
        by_router_kind=_rows(
            await metrics_repo.count_by(session, column="router_kind", since=since)
        ),
    )
