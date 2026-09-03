"""admin_audit_log 기록. 쿼리만 있고 판단은 없습니다.

"무엇을 감사 대상으로 볼 것인가"와 "언제 확정할 것인가"는 services/audit.py 가 정합니다.

commit 은 하지 않습니다. 트랜잭션 경계는 services 가 잡습니다 — 다만 이 테이블에서는
그 경계가 평소보다 까다롭습니다. services/audit.py 의 docstring 을 보세요.
"""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import AdminAuditLog


async def add(
    session: AsyncSession,
    *,
    action: str,
    admin_user_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    detail: dict[str, Any] | None = None,
    request_id: str | None = None,
    ip: str | None = None,
) -> AdminAuditLog:
    """감사 행 하나를 세션에 얹습니다.

    `admin_user_id` 가 None 인 것은 "빠뜨렸다"가 아니라 **주체를 특정할 수 없는
    행위**라는 뜻입니다 (없는 아이디로 두드린 로그인 실패). 그래서 기본값이 있습니다.

    `detail` 에 복호화된 개인정보를 넣지 마세요 (models/admin_audit_log.py).
    """
    entry = AdminAuditLog(
        admin_user_id=admin_user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
        request_id=request_id,
        ip=ip,
    )
    session.add(entry)
    # id 와 created_at 은 DB 가 채웁니다. 여기서 flush 해 두면 제약 위반(예: 없는
    # admin_user_id)이 나중이 아니라 이 자리에서 드러납니다.
    # commit 은 아니므로 롤백은 그대로 됩니다.
    await session.flush()
    return entry
