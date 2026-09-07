"""AI 답변 신고 (D-053 · 콘솔 로드맵 A1).

앱은 신고를 넣고, 관리자는 그것을 열어 처리합니다. **열람 범위는 D-053 이 정했고 이
파일이 그것을 지키는 자리입니다** — 원문은 신고된 turn 하나, 나머지는 숫자뿐입니다.

--------------------------------------------------------------------------------
**감사 기록의 순서** — `services/app_user_admin.py` 의 `reveal` 과 같은 모양입니다.

상세를 열 때는 **기록을 먼저 확정하고 원문을 돌려줍니다.** `record_and_commit` 이
실패하면 예외가 그대로 올라가고 호출자는 원문을 못 받습니다. 반대로 했다면 "본 사람은
있는데 기록은 없는" 조회가 생기고, 그건 감사 로그가 있는 것이 없는 것보다 나쁜
상태입니다 (`services/audit.py`).

**목록은 남기지 않습니다** (D-053 ③). `pii_revealed` 가 그은 선과 같습니다 — 남길
가치가 있는 것은 "가려서 봤다" 가 아니라 "원문을 열어 봤다" 입니다.
--------------------------------------------------------------------------------
"""

import base64
import binascii
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.tracing import record_report_feedback
from daengs_backend.models import (
    AUDIT_REPORT_RESOLVED,
    AUDIT_REPORT_TURN_REVEALED,
    AnswerReport,
    ChatTurn,
)
from daengs_backend.repositories import answer_report as report_repo
from daengs_backend.services import audit

__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "AlreadyReportedError",
    "InvalidCursorError",
    "ReportDetailView",
    "ReportPageView",
    "ReportView",
    "TurnNotFoundError",
    "create",
    "get_detail",
    "list_reports",
    "set_status",
]

#: 한 번에 주는 최대 행 수. 화면의 "더 보기" 가 이 단위로 부릅니다.
MAX_LIMIT = 200
DEFAULT_LIMIT = 50


class TurnNotFoundError(Exception):
    """없는 turn 이거나 **남의 turn** 입니다. 라우터가 404 로 바꿉니다.

    둘을 구분해서 알려 주지 않습니다 — 구분하면 남의 turn id 가 실재하는지
    확인하는 길이 됩니다.
    """


class AlreadyReportedError(Exception):
    """이미 이 사람이 이 답변을 신고했습니다. 라우터가 409 로 바꿉니다."""


class InvalidCursorError(Exception):
    """커서가 우리가 만든 값이 아닙니다. 라우터가 422 로 바꿉니다."""


@dataclass(frozen=True)
class ReportView:
    report: AnswerReport
    reviewer_login_id: str | None
    reviewer_name: str | None
    report_count: int


@dataclass(frozen=True)
class ReportPageView:
    reports: list[ReportView]
    next_cursor: str | None


@dataclass(frozen=True)
class ReportDetailView:
    view: ReportView
    turn: ChatTurn
    session_turn_count: int
    position: int


def _encode_cursor(at: datetime, report_id: uuid.UUID) -> str:
    """`(created_at, id)` 를 불투명한 문자열로 (감사 목록과 같은 방식)."""
    raw = f"{at.isoformat()}|{report_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        at_text, _, id_text = raw.partition("|")
        return datetime.fromisoformat(at_text), uuid.UUID(id_text)
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise InvalidCursorError from None


# ---------------------------------------------------------------------------
# 앱 쪽
# ---------------------------------------------------------------------------


async def create(
    session: AsyncSession,
    *,
    turn_id: uuid.UUID,
    app_user_id: uuid.UUID,
    reason: str,
) -> AnswerReport:
    """신고를 받습니다. **본인 대화의 turn 만** 신고할 수 있습니다.

    소유 확인을 리포지토리의 조인에 맡깁니다 (`get_owned_turn`) — 여기서
    `session.get(ChatTurn, ...)` 을 쓰면 **남의 답변을 신고할 수 있게 되고**, 그러면
    관리자 화면에 신고자와 무관한 남의 대화가 뜹니다.

    중복은 DB 의 `answer_reports_turn_user_key` 가 막습니다. 먼저 SELECT 해서 확인하지
    않는 것은 **같은 사람이 두 번 눌렀을 때 그 사이가 비기** 때문입니다 — 유니크
    제약이 유일하게 확실한 자리입니다.
    """
    turn = await report_repo.get_owned_turn(
        session, turn_id=turn_id, app_user_id=app_user_id
    )
    if turn is None:
        raise TurnNotFoundError

    # 커밋 전에 읽어 둡니다 — 커밋이 속성을 만료시키므로 뒤에서 읽으면 SELECT 가
    # 한 번 더 나가고, 그건 아래 외부 호출 동안 트랜잭션을 다시 여는 일입니다.
    traced_request_id = turn.request_id

    report = await report_repo.add(
        session, turn_id=turn_id, app_user_id=app_user_id, reason=reason
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise AlreadyReportedError from None

    # **커밋 다음, refresh 앞입니다.** 여기가 이 함수에서 열린 트랜잭션도 행 잠금도
    # 없는 유일한 지점이라, 외부 호출을 둘 자리가 여기뿐입니다 (D-048 의 경계).
    # 트레이싱이 꺼져 있으면 즉시 돌아오고, 켜져 있어도 실패는 삼킵니다 — 신고는
    # 이미 저장됐습니다 (D-054).
    await record_report_feedback(request_id=traced_request_id)

    await session.refresh(report)
    return report


# ---------------------------------------------------------------------------
# 관리자 쪽
# ---------------------------------------------------------------------------


async def list_reports(
    session: AsyncSession,
    *,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
    status: str | None = None,
) -> ReportPageView:
    """최근 순 한 쪽. **감사에 남기지 않습니다** (D-053 ③).

    `limit + 1` 개를 읽어 다음 쪽이 있는지 봅니다 — 정확히 `limit` 개면 "이게
    마지막인가"를 알 수 없어 화면이 빈 쪽을 한 번 더 부릅니다.
    """
    capped = max(1, min(limit, MAX_LIMIT))
    before = _decode_cursor(cursor) if cursor else None

    rows = await report_repo.list_reports(
        session, limit=capped + 1, before=before, status=status
    )
    has_more = len(rows) > capped
    kept = rows[:capped]

    counts = await report_repo.count_for_turns(session, [r[0].turn_id for r in kept])
    reports = [
        ReportView(
            report=row[0],
            reviewer_login_id=row[1],
            reviewer_name=row[2],
            report_count=counts.get(row[0].turn_id, 0),
        )
        for row in kept
    ]
    next_cursor = (
        _encode_cursor(reports[-1].report.created_at, reports[-1].report.id)
        if has_more and reports
        else None
    )
    return ReportPageView(reports=reports, next_cursor=next_cursor)


async def get_detail(
    session: AsyncSession,
    *,
    report_id: uuid.UUID,
    actor_id: uuid.UUID,
    ip: str | None = None,
) -> ReportDetailView | None:
    """신고 하나 + **그 turn 원문 하나**. 없으면 `None`.

    **부를 때마다 감사 행이 남습니다** — 기록을 먼저 확정하고 원문을 돌려줍니다
    (이 파일 머리말). 그래서 이 호출은 멱등하지 않습니다. GET 인 것은 클라이언트
    입장에서 자원을 바꾸지 않기 때문이고, **브라우저가 미리 불러 두는 자리에 이
    경로를 두지 마세요.**

    `target_type` 은 `app_user` 이고 대상은 **신고자**입니다 — 나중에 따질 것이
    "누구의 대화를 봤나" 이기 때문입니다. 어느 신고였는지는 `detail.report_id` 로
    남습니다. **`detail` 에 원문을 넣지 마세요** — 넣으면 `admin_audit_log` 가 두
    번째 대화 저장소가 됩니다.
    """
    row = await report_repo.get_with_reviewer(session, report_id)
    if row is None:
        return None

    report: AnswerReport = row[0]
    turn = await report_repo.get_turn(session, report.turn_id)
    if turn is None:
        # FK 가 CASCADE 라 정상적으로는 신고만 남을 수 없습니다. 그래도 여기서 500 을
        # 내지 않는 것은, 그 상태가 되면 화면이 "없는 신고" 로 보는 쪽이 안전해서입니다.
        return None

    total, position = await report_repo.turn_position(session, turn)
    count = await report_repo.count_for_turn(session, report.turn_id)

    await audit.record_and_commit(
        session,
        action=AUDIT_REPORT_TURN_REVEALED,
        admin_user_id=actor_id,
        target_type="app_user",
        target_id=report.app_user_id,
        detail={"report_id": str(report.id)},
        ip=ip,
    )

    return ReportDetailView(
        view=ReportView(
            report=report,
            reviewer_login_id=row[1],
            reviewer_name=row[2],
            report_count=count,
        ),
        turn=turn,
        session_turn_count=total,
        position=position,
    )


async def set_status(
    session: AsyncSession,
    *,
    report_id: uuid.UUID,
    status: str,
    actor_id: uuid.UUID,
    ip: str | None = None,
) -> AnswerReport | None:
    """처리 상태를 바꿉니다. 없으면 `None`.

    **`reviewed_by` 와 `reviewed_at` 을 같이 채웁니다** — SQL 의
    `answer_reports_review_state_check` 가 한쪽만 채워진 행을 막습니다. 그 제약이
    있는 이유는 "누가 처리했나" 를 세는 것이 집계가 아니라 추측이 되지 않게 하는
    것입니다.

    같은 상태로 다시 눌러도 처리한 사람과 시각을 **갱신합니다** — 마지막으로 만진
    사람이 답하는 쪽이 맞습니다.
    """
    report = await report_repo.get_by_id(session, report_id)
    if report is None:
        return None

    report.status = status
    report.reviewed_by = actor_id
    report.reviewed_at = datetime.now(UTC)

    await audit.record(
        session,
        action=AUDIT_REPORT_RESOLVED,
        admin_user_id=actor_id,
        target_type="app_user",
        target_id=report.app_user_id,
        detail={"report_id": str(report.id), "status": status},
        ip=ip,
    )
    await session.commit()
    await session.refresh(report)
    return report
