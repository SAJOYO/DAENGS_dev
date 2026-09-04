"""신고 조회·처리 HTTP 경계 (D-053 · 콘솔 로드맵 A1).

판단은 여기 없습니다. 커서·한 쪽의 크기·감사 기록 시점은 `services/answer_report.py` 가
정하고, 여기서는 그 결과를 스키마로 옮기기만 합니다.

--------------------------------------------------------------------------------
**권한은 `admin:manage`(ADMIN 만)입니다** — D-053 ② (2026-09-04 사람 결정).

`pii:read` 에 얹지 않은 것은 그 권한이 "`app_users` 의 암호문을 원문으로 여는 일" 이라
뜻이 다르기 때문이고, `report:read` 같은 새 권한을 세우지 않은 것은 지금 그것으로 갈릴
계정이 없기 때문입니다.

**지금은 이 잠금이 아무도 가리지 않습니다** — 발급된 계정이 전부 ADMIN 입니다
(`core/deps.py` 의 `ROLE_PERMISSIONS`). 실제로 갈리는 것은 로드맵 §7 "역할 발급" 이
닫힌 뒤이고, 그래도 지금 박아 두는 것은 **나중에 좁히는 일이 넓히는 일보다 비싸기**
때문입니다.
--------------------------------------------------------------------------------

**목록은 감사에 남기지 않고, 상세만 남깁니다** (D-053 ③) — `services/answer_report.py`.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import Perm, Principal, client_ip, require
from daengs_backend.repositories import answer_report as report_repo
from daengs_backend.schemas.answer_report import (
    ReportDetail,
    ReportedTurn,
    ReportListItem,
    ReportPage,
    ReportReviewer,
    ReportStatusPatch,
)
from daengs_backend.services import answer_report as report_service

router = APIRouter(prefix="/admin/reports", tags=["admin-reports"])


def _reviewer(view: report_service.ReportView) -> ReportReviewer | None:
    """처리 전이면 `None` 입니다 — 목록에서 제일 보고 싶은 행이 그것입니다."""
    if (
        view.report.reviewed_by is None
        or view.reviewer_login_id is None
        or view.reviewer_name is None
    ):
        return None
    return ReportReviewer(
        id=view.report.reviewed_by,
        login_id=view.reviewer_login_id,
        name=view.reviewer_name,
    )


@router.get("", response_model=ReportPage)
async def list_reports(
    _admin: Annotated[Principal, Depends(require(Perm.ADMIN_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=report_service.MAX_LIMIT)] = (
        report_service.DEFAULT_LIMIT
    ),
    report_status: Annotated[
        str | None, Query(alias="status", pattern="^(open|reviewed|dismissed)$")
    ] = None,
) -> ReportPage:
    """최근 순 한 쪽. `next_cursor` 를 그대로 돌려주면 다음 쪽입니다.

    **원문이 없습니다.** 목록을 여는 것은 감사에 남기지 않으므로(D-053 ③) 여기에
    질문·답변을 실으면 그 선이 무너집니다 — 원문은 상세에서만 나갑니다.

    **총 개수를 주지 않습니다.** 감사 목록과 같은 이유로, 읽는 사이에도 늘어서 곧
    틀린 숫자가 됩니다.
    """
    try:
        page = await report_service.list_reports(
            session, limit=limit, cursor=cursor, status=report_status
        )
    except report_service.InvalidCursorError:
        # 커서는 URL 에 실려 오므로 손으로 고친 값이 들어올 수 있습니다.
        # 서버 잘못이 아니라 잘못된 요청이라 422 입니다 (500 이 아닙니다).
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "커서가 올바르지 않습니다."
        ) from None

    return ReportPage(
        reports=[
            ReportListItem(
                id=v.report.id,
                created_at=v.report.created_at,
                status=v.report.status,  # type: ignore[arg-type]
                reason=v.report.reason,
                report_count=v.report_count,
                reviewer=_reviewer(v),
                reviewed_at=v.report.reviewed_at,
            )
            for v in page.reports
        ],
        next_cursor=page.next_cursor,
    )


@router.get("/{report_id}", response_model=ReportDetail)
async def get_report(
    report_id: uuid.UUID,
    request: Request,
    admin: Annotated[Principal, Depends(require(Perm.ADMIN_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReportDetail:
    """신고 하나 + **신고된 답변 한 건**의 원문.

    **GET 인데 쓰기가 일어납니다.** 감사 기록이 그 쓰기이고, 그래서 이 호출은 멱등하지
    않습니다. GET 인 것은 클라이언트 입장에서 자원을 바꾸지 않기 때문이지만,
    **브라우저가 미리 불러 두는 자리에 이 경로를 두지 마세요**
    (`routers/app_user_admin.py` 의 `reveal` 과 같은 모양입니다).

    **이 대화의 다른 turn 은 나가지 않습니다** (D-053 ①). 대신 `session_turn_count` 와
    `position` 이 "몇 턴짜리 대화의 몇 번째인지" 를 **숫자로만** 알려 줍니다 — 맥락에
    기댄 답변인지 판단하고, 나중에 열람 범위를 넓힐지 정할 때 추측이 아니라 실측이
    근거가 되게 하는 값입니다. `position` 이 0 이면 그 turn 이 완료 상태가 아니라
    순번을 매길 자리가 없다는 뜻입니다.
    """
    detail = await report_service.get_detail(
        session,
        report_id=report_id,
        actor_id=admin.admin_id,
        ip=client_ip(request),
    )
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "없는 신고입니다.")

    v = detail.view
    return ReportDetail(
        id=v.report.id,
        created_at=v.report.created_at,
        status=v.report.status,  # type: ignore[arg-type]
        reason=v.report.reason,
        report_count=v.report_count,
        reviewer=_reviewer(v),
        reviewed_at=v.report.reviewed_at,
        turn=ReportedTurn(
            id=detail.turn.id,
            created_at=detail.turn.created_at,
            user_content=detail.turn.user_content,
            assistant_content=detail.turn.assistant_content,
            assistant_status=detail.turn.assistant_status,
            public_response=detail.turn.public_response,
            request_id=detail.turn.request_id,
            agent_categories=list(detail.turn.agent_categories or []),
            session_turn_count=detail.session_turn_count,
            position=detail.position,
        ),
    )


@router.patch("/{report_id}", response_model=ReportListItem)
async def update_status(
    report_id: uuid.UUID,
    body: ReportStatusPatch,
    request: Request,
    admin: Annotated[Principal, Depends(require(Perm.ADMIN_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReportListItem:
    """처리 / 기각.

    **`open` 으로 되돌리는 것은 받지 않습니다** (`schemas/answer_report.py`) — 처리한
    사람과 시각을 지우는 일이라 감사 기록과 어긋납니다.

    응답에 원문이 없습니다 — 처리는 목록 화면에서 하는 일이고, 원문이 필요하면 상세를
    열어야 하며 **그때 감사에 남습니다.**
    """
    report = await report_service.set_status(
        session,
        report_id=report_id,
        status=body.status,
        actor_id=admin.admin_id,
        ip=client_ip(request),
    )
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "없는 신고입니다.")

    # 방금 바꾼 행 하나만 다시 조립합니다. 목록 조회를 재사용하지 않는 것은 그쪽이
    # 커서 한 쪽 전체를 읽기 때문입니다.
    count = await report_repo.count_for_turn(session, report.turn_id)
    reviewer_row = await report_repo.get_with_reviewer(session, report.id)
    view = report_service.ReportView(
        report=report,
        reviewer_login_id=reviewer_row[1] if reviewer_row is not None else None,
        reviewer_name=reviewer_row[2] if reviewer_row is not None else None,
        report_count=count,
    )
    return ReportListItem(
        id=report.id,
        created_at=report.created_at,
        status=report.status,  # type: ignore[arg-type]
        reason=report.reason,
        report_count=count,
        reviewer=_reviewer(view),
        reviewed_at=report.reviewed_at,
    )
