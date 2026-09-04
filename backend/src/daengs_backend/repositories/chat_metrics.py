"""대화 집계 쿼리 (콘솔 로드맵 B3 · #223). 세는 것만 하고 판단은 없습니다.

**`repositories/chat.py` 와 나눠 둔 이유**는 읽는 사람이 달라서입니다. 저기는 앱 회원이
**자기** 대화를 만들고 여는 수명주기고, 여기는 관리자가 **전원의** 행을 세는 것입니다.
같은 테이블을 보지만 소유권 조건이 아예 없다는 점에서 성격이 반대라, 한 파일에 두면
"이 함수에 app_user_id 가 왜 없지" 를 매번 다시 확인하게 됩니다.

--------------------------------------------------------------------------------
**원문 컬럼을 SELECT 하지 않습니다.**

`user_content` · `assistant_content` · `public_response` 는 이 파일의 어느 쿼리에도
나오지 않습니다. D-037 이 관측에 질문 원문을 금지했고, 이 카드의 값은 **원문 없이도
알 수 있다**는 것입니다. 세는 함수에 원문이 필요해지는 날이 오면 그건 집계가 아니라
다른 일입니다.
--------------------------------------------------------------------------------
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import ChatSession, ChatSummary, ChatTurn

#: `error_code` 는 값이 자유롭게 늘 수 있어 상위 몇 개만 봅니다.
_TOP_ERROR_CODES = 10


async def count_sessions(session: AsyncSession, *, since: datetime) -> int:
    """기간 안에 만들어진 세션 수."""
    stmt = select(func.count()).select_from(ChatSession).where(
        ChatSession.created_at >= since
    )
    return await session.scalar(stmt) or 0


async def count_turns_by_processing_status(
    session: AsyncSession, *, since: datetime
) -> dict[str, int]:
    """`processing` · `completed` · `failed` 별 turn 수.

    **`assistant_status` 와 합치지 마세요.** `07_chats.sql` 의 `chat_turns_state_check`
    가 셋을 못 겹치게 묶어 두었습니다 — `failed` 인 행은 `assistant_status` 가 반드시
    NULL 입니다. 즉 "처리가 죽은 것" 과 "어시스턴트가 FAILED 로 답한 것" 은 **다른
    행**이고, 고칠 사람도 다릅니다.
    """
    stmt = (
        select(ChatTurn.processing_status, func.count())
        .where(ChatTurn.created_at >= since)
        .group_by(ChatTurn.processing_status)
    )
    return {row[0]: row[1] for row in (await session.execute(stmt)).all()}


async def count_turns_by_assistant_status(
    session: AsyncSession, *, since: datetime
) -> dict[str, int]:
    """어시스턴트 응답 결과 분포. 8값입니다 (07_chats.sql 의 CHECK).

    `ANSWERED` · `PARTIAL` · `CLARIFY` · `HANDOFF` · `UNCERTAIN` · `REFUSED` ·
    `PENDING` · `FAILED`.

    **NULL 은 빼고 셉니다.** NULL 인 행은 아직 도는 중이거나 처리가 죽은 것이라
    "어시스턴트가 어떻게 답했나" 에 들어갈 값이 없습니다 (state CHECK). 분모를 그
    행까지 넣으면 시점마다 비율이 흔들립니다.
    """
    stmt = (
        select(ChatTurn.assistant_status, func.count())
        .where(ChatTurn.created_at >= since, ChatTurn.assistant_status.is_not(None))
        .group_by(ChatTurn.assistant_status)
    )
    return {row[0]: row[1] for row in (await session.execute(stmt)).all()}


async def count_error_codes(
    session: AsyncSession, *, since: datetime
) -> list[tuple[str, int]]:
    """처리가 죽은 turn 의 `error_code` 상위 몇 개. 많은 순입니다.

    `processing_status='failed'` 인 행에만 `error_code` 가 있습니다 (state CHECK).
    """
    stmt = (
        select(ChatTurn.error_code, func.count().label("n"))
        .where(
            ChatTurn.created_at >= since,
            ChatTurn.processing_status == "failed",
            ChatTurn.error_code.is_not(None),
        )
        .group_by(ChatTurn.error_code)
        .order_by(func.count().desc())
        .limit(_TOP_ERROR_CODES)
    )
    return [(row[0], row[1]) for row in (await session.execute(stmt)).all()]


async def count_agent_categories(
    session: AsyncSession, *, since: datetime
) -> list[tuple[str, int]]:
    """능력별 태그 수. 많은 순입니다.

    ⚠ **합이 turn 수가 아닙니다.** `agent_categories` 는 `ARRAY(Text)` 라 turn 하나가
    여러 태그를 답니다. 그래서 이 값들의 합은 turn 수보다 클 수 있고, 화면이 turn 수를
    분모로 비율을 그리면 100%를 넘습니다.

    `unnest` 를 서브쿼리로 감싸는 것은 집합 반환 함수를 GROUP BY 대상으로 쓰기
    위해서입니다 — SELECT 목록에 두고 바로 그룹핑하면 Postgres 가 LATERAL 로 풀면서
    읽기 어려운 계획이 나옵니다.
    """
    unnested = (
        select(func.unnest(ChatTurn.agent_categories).label("category"))
        .where(ChatTurn.created_at >= since)
        .subquery()
    )
    stmt = (
        select(unnested.c.category, func.count().label("n"))
        .group_by(unnested.c.category)
        .order_by(func.count().desc())
    )
    return [(row[0], row[1]) for row in (await session.execute(stmt)).all()]


async def count_summaries_by_processing_status(
    session: AsyncSession, *, since: datetime
) -> dict[str, int]:
    """AI 요약 생성 결과 분포. `chat_summaries` 도 `processing`·`completed`·`failed` 입니다."""
    stmt = (
        select(ChatSummary.processing_status, func.count())
        .where(ChatSummary.created_at >= since)
        .group_by(ChatSummary.processing_status)
    )
    return {row[0]: row[1] for row in (await session.execute(stmt)).all()}
