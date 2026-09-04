"""감사 로그 조회 HTTP 경계 (콘솔 로드맵 A4-1 · #221).

판단은 여기 없습니다. 커서를 굽고 푸는 것과 한 쪽의 크기는 `services/audit.py` 가 정하고,
여기서는 그 결과를 스키마로 옮기기만 합니다.

--------------------------------------------------------------------------------
**권한은 `admin:manage`(ADMIN 만)입니다** — 2026-09-04 사람 결정.

이 화면에는 "누가 어느 회원의 개인정보를 열었나"와 "어떤 아이디로 로그인이 시도됐나"가
그대로 보입니다. 감사 로그를 보는 것 자체가 **동료를 감시할 수 있다**는 뜻이라, 계정
관리와 같은 등급으로 묶었습니다.

그래서 **`OPERATOR` 는 복호화는 하지만 누가 복호화했는지는 못 봅니다.** 비대칭이지만
의도한 것입니다 — 감사의 값은 "본 사람이 나중에 따져질 수 있다" 에서 나오고, 보는 쪽과
따지는 쪽이 같으면 그 값이 줄어듭니다.

새 `Perm`(`audit:read`)을 만들지 않은 것은 D-014 의 5단계를 늘리는 결정을 이 카드에서
하지 않으려는 것입니다 (`core/deps.py` 의 `Perm` 주석).
--------------------------------------------------------------------------------

**이 조회 자체는 감사에 남기지 않습니다** — 이유는 `services/audit.py` 의 읽기 절 주석.
"""

import logging
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session
from daengs_backend.core.deps import Perm, Principal, require
from daengs_backend.schemas.admin_audit import (
    AuditActorOut,
    AuditEntryOut,
    AuditPageOut,
)
from daengs_backend.services import audit as audit_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/audit", tags=["admin-audit"])


@router.get("", response_model=AuditPageOut)
async def list_entries(
    _admin: Annotated[Principal, Depends(require(Perm.ADMIN_MANAGE))],
    session: Annotated[AsyncSession, Depends(get_session)],
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=audit_service.MAX_LIMIT)] = (
        audit_service.DEFAULT_LIMIT
    ),
    action_prefix: Annotated[str | None, Query(max_length=60)] = None,
    admin_user_id: uuid.UUID | None = None,
    target_id: uuid.UUID | None = None,
    since: datetime | None = None,
) -> AuditPageOut:
    """최근 순 한 쪽. `next_cursor` 를 그대로 돌려주면 다음 쪽입니다.

    **총 개수를 주지 않습니다.** 세는 값이 비싸고 읽는 사이에도 늘어서(로그인마다 행이
    생깁니다) 곧 틀린 숫자가 됩니다 (`schemas/admin_audit.py`).

    `action_prefix` 는 접두어입니다 — `admin.app_user.` 로 갈래 전체를,
    `admin.app_user.pii_revealed` 로 하나만 고릅니다. 세 갈래는
    `models/admin_audit_log.py` 의 `AUDIT_ACTIONS` 에 있습니다.
    """
    try:
        page = await audit_service.list_entries(
            session,
            limit=limit,
            cursor=cursor,
            action_prefix=action_prefix,
            admin_user_id=admin_user_id,
            target_id=target_id,
            since=since,
        )
    except audit_service.InvalidCursorError:
        # 커서는 URL 에 실려 오므로 손으로 고친 값이 들어올 수 있습니다.
        # 서버 잘못이 아니라 잘못된 요청이라 422 입니다 (500 이 아닙니다).
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "커서가 올바르지 않습니다."
        ) from None

    return AuditPageOut(
        entries=[
            AuditEntryOut(
                id=e.entry.id,
                created_at=e.entry.created_at,
                action=e.entry.action,
                # **주체가 없는 것이 정상입니다** — 없는 아이디로 두드린 로그인 실패.
                # 무엇을 시도했는지는 `detail.login_id` 에만 있습니다.
                actor=(
                    AuditActorOut(
                        id=e.entry.admin_user_id,
                        login_id=e.actor_login_id,
                        name=e.actor_name,
                    )
                    if e.entry.admin_user_id is not None
                    and e.actor_login_id is not None
                    and e.actor_name is not None
                    else None
                ),
                target_type=e.entry.target_type,
                target_id=e.entry.target_id,
                detail=e.entry.detail,
                ip=str(e.entry.ip) if e.entry.ip is not None else None,
                request_id=e.entry.request_id,
            )
            for e in page.entries
        ],
        next_cursor=page.next_cursor,
    )
