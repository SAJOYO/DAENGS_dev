"""앱 회원의 AI 답변 신고 HTTP 경계 (D-053 · 콘솔 로드맵 A1).

판단은 여기 없습니다. "본인 것인가"·"이미 신고했나"는 `services/answer_report.py` 가
정하고, 여기서는 그 예외를 상태 코드로 바꾸기만 합니다.

**경로가 `/app/reports` 인 이유**는 앱 회원 전용이기 때문입니다 (`/app/chats` 와 같은
규칙). `CurrentAppUser` 를 쓰므로 관리자 토큰은 여기서 401 입니다 — 신원으로 남의 것을
걸러야 하는 API 라 그렇습니다 (`core/deps.py`).

**관리자용 조회는 여기 없습니다** — `routers/report_admin.py` 입니다. 한 파일에 두면
`Perm` 이 다른 두 문이 같은 라우터에 붙어, 나중에 하나를 옮길 때 다른 하나가 따라옵니다.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import CurrentAppUser
from daengs_backend.schemas.answer_report import ReportCreate, ReportCreated
from daengs_backend.services import answer_report as report_service

router = APIRouter(prefix="/app/reports", tags=["reports"])


@router.post("", response_model=ReportCreated, status_code=status.HTTP_201_CREATED)
async def create_report(
    body: ReportCreate,
    member: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReportCreated:
    """답변 하나를 신고합니다.

    **본인 대화의 turn 만 신고할 수 있습니다.** 남의 `turn_id` 를 넣으면 404 이고,
    없는 id 와 구분해 주지 않습니다 — 구분하면 남의 turn id 가 실재하는지 확인하는
    길이 됩니다.

    **같은 답변을 두 번 신고하면 409 입니다.** 여러 사람이 같은 답변을 신고하는 것은
    막지 않습니다 — 몇 번 신고됐는지가 그 답변이 얼마나 나쁜지의 신호입니다.

    **답변 원문을 받지 않습니다.** `turn_id` 가 `chat_turns` 한 행을 가리키고 질문 ·
    답변 · 근거가 전부 거기 있습니다 (D-048). 신고 표에 복사하면 탈퇴 시 파기 대상이
    둘이 됩니다.

    ⚠ **이 API 만으로는 신고를 켜지 마세요.** D-053 ④ 가 앱 화면의 고지 한 줄을
    "앱이 이 API 를 부르기 시작하는 것" 의 전제로 걸어 뒀습니다.
    """
    try:
        report = await report_service.create(
            session,
            turn_id=body.turn_id,
            app_user_id=member.app_user_id,
            reason=body.reason,
        )
    except report_service.TurnNotFoundError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "없는 답변입니다."
        ) from None
    except report_service.AlreadyReportedError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "이미 신고한 답변입니다."
        ) from None

    return ReportCreated(id=report.id, created_at=report.created_at)
