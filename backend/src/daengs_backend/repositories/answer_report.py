"""answer_reports 쿼리. 판단은 없습니다 — services/answer_report.py 가 합니다.

commit 은 하지 않습니다. 트랜잭션 경계는 services 가 잡습니다.

**이 파일에서 `chat_turns` 를 세션 단위로 긁는 쿼리를 만들지 마세요.** D-053 이 열람을
신고된 turn 하나로 좁혔고, 여기에 "세션의 turn 전부" 를 돌려주는 함수가 생기는 순간
그것을 부르는 코드가 따라옵니다. 필요한 것은 **개수와 순번**뿐이고 그건 아래 두 함수가
원문 없이 돌려줍니다.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Row, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import AdminUser, AnswerReport, ChatSession, ChatTurn


async def add(
    session: AsyncSession,
    *,
    turn_id: uuid.UUID,
    app_user_id: uuid.UUID,
    reason: str,
) -> AnswerReport:
    """신고 하나를 세션에 얹습니다. 확정은 services 가 합니다."""
    report = AnswerReport(turn_id=turn_id, app_user_id=app_user_id, reason=reason)
    session.add(report)
    return report


async def get_owned_turn(
    session: AsyncSession, *, turn_id: uuid.UUID, app_user_id: uuid.UUID
) -> ChatTurn | None:
    """**그 회원의 것인 turn 만** 돌려줍니다. 남의 것이면 `None` 입니다.

    `chat_turns` 에는 소유자 열이 없어서 `chat_sessions` 를 거쳐야 합니다 — 그래서
    이 조인을 빠뜨리면 **아무 turn 이나 신고할 수 있게 됩니다.** 남의 대화 id 를
    넣어 신고하면 관리자 화면에 남의 대화가 뜨는 길이 열립니다.
    """
    stmt = (
        select(ChatTurn)
        .join(ChatSession, ChatSession.id == ChatTurn.session_id)
        .where(ChatTurn.id == turn_id, ChatSession.app_user_id == app_user_id)
    )
    return await session.scalar(stmt)


async def get_by_id(session: AsyncSession, report_id: uuid.UUID) -> AnswerReport | None:
    return await session.get(AnswerReport, report_id)


async def get_turn(session: AsyncSession, turn_id: uuid.UUID) -> ChatTurn | None:
    """관리자용 — 소유자를 안 봅니다. 신고 행이 이미 그 turn 을 가리키고 있습니다."""
    return await session.get(ChatTurn, turn_id)


async def count_for_turn(session: AsyncSession, turn_id: uuid.UUID) -> int:
    """이 답변이 몇 번 신고됐는지. `answer_reports_turn_idx` 가 받습니다."""
    stmt = select(func.count()).select_from(AnswerReport).where(
        AnswerReport.turn_id == turn_id
    )
    return int(await session.scalar(stmt) or 0)


async def count_for_turns(
    session: AsyncSession, turn_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """목록용 — 한 번에 셉니다.

    **행마다 `count_for_turn` 을 부르지 마세요.** 한 쪽이 50줄이면 쿼리가 51번 나갑니다.
    """
    if not turn_ids:
        return {}
    stmt = (
        select(AnswerReport.turn_id, func.count())
        .where(AnswerReport.turn_id.in_(list(turn_ids)))
        .group_by(AnswerReport.turn_id)
    )
    result = await session.execute(stmt)
    return {row[0]: int(row[1]) for row in result.all()}


async def turn_position(
    session: AsyncSession, turn: ChatTurn
) -> tuple[int, int]:
    """`(그 대화의 완료 turn 총 수, 이 turn 이 몇 번째인지)`.

    **원문을 읽지 않습니다 — 세기만 합니다** (D-053 ①). 이 숫자가 있어야 관리자가
    "앞 맥락에 기댄 답변이라 이 한 건만으로는 판단이 안 된다" 를 알 수 있고, 나중에
    열람 범위를 넓힐지 정할 때 추측이 아니라 실측이 근거가 됩니다.

    세는 대상은 **완료된 turn** 입니다 — 처리 중이거나 실패한 turn 은 사용자에게 답이
    가지 않았으므로 대화의 길이로 치지 않습니다. 순번은 `created_at` 오름차순이고,
    `chat_turns_session_order_idx (session_id, created_at, id)` 가 받습니다.
    """
    completed = ChatTurn.processing_status == "completed"

    total = int(
        await session.scalar(
            select(func.count())
            .select_from(ChatTurn)
            .where(ChatTurn.session_id == turn.session_id, completed)
        )
        or 0
    )

    if turn.processing_status != "completed":
        # 신고된 turn 자체가 완료가 아니면 순번을 매길 자리가 없습니다.
        # 0 은 "이 대화에서 몇 번째인지 말할 수 없다" 는 뜻입니다.
        return total, 0

    before = int(
        await session.scalar(
            select(func.count())
            .select_from(ChatTurn)
            .where(
                ChatTurn.session_id == turn.session_id,
                completed,
                (ChatTurn.created_at, ChatTurn.id) < (turn.created_at, turn.id),  # type: ignore[operator]
            )
        )
        or 0
    )
    return total, before + 1


async def list_reports(
    session: AsyncSession,
    *,
    limit: int,
    before: tuple[datetime, uuid.UUID] | None = None,
    status: str | None = None,
) -> Sequence[Row[tuple[AnswerReport, str | None, str | None]]]:
    """최근 순 한 쪽 + 처리한 관리자의 login_id · 이름.

    **LEFT JOIN 입니다** — 아직 처리 안 된 신고는 `reviewed_by` 가 NULL 이고, 그것이
    목록에서 제일 보고 싶은 행입니다. INNER JOIN 으로 바꾸면 그 행들만 사라집니다.

    키셋 페이지네이션(`before` = 마지막으로 본 `(created_at, id)`)인 이유는 감사 목록과
    같습니다 — 읽는 동안에도 신고가 들어오므로 OFFSET 이면 건너뛰거나 겹칩니다.
    `answer_reports_created_idx` 와 `answer_reports_status_created_idx` 가 받습니다.
    """
    stmt = (
        select(AnswerReport, AdminUser.login_id, AdminUser.name)
        .join(AdminUser, AdminUser.id == AnswerReport.reviewed_by, isouter=True)
        .order_by(AnswerReport.created_at.desc(), AnswerReport.id.desc())
        .limit(limit)
    )
    if before is not None:
        at, last_id = before
        stmt = stmt.where(
            (AnswerReport.created_at, AnswerReport.id) < (at, last_id)  # type: ignore[operator]
        )
    if status is not None:
        stmt = stmt.where(AnswerReport.status == status)

    result = await session.execute(stmt)
    return result.all()


async def get_with_reviewer(
    session: AsyncSession, report_id: uuid.UUID
) -> Row[tuple[AnswerReport, str | None, str | None]] | None:
    stmt = (
        select(AnswerReport, AdminUser.login_id, AdminUser.name)
        .join(AdminUser, AdminUser.id == AnswerReport.reviewed_by, isouter=True)
        .where(AnswerReport.id == report_id)
    )
    result = await session.execute(stmt)
    return result.first()
