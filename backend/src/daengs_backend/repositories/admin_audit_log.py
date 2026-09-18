"""admin_audit_log 기록과 조회. 쿼리만 있고 판단은 없습니다.

"무엇을 감사 대상으로 볼 것인가"와 "언제 확정할 것인가"는 services/audit.py 가 정합니다.

commit 은 하지 않습니다. 트랜잭션 경계는 services 가 잡습니다 — 다만 이 테이블에서는
그 경계가 평소보다 까다롭습니다. services/audit.py 의 docstring 을 보세요.

**읽는 쪽(`list_entries`)은 #221 이 더했습니다.** 그전까지는 쓰기만 있었고 보는 길이
psql 뿐이었습니다.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Row, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import AdminAuditLog, AdminUser


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


async def list_entries(
    session: AsyncSession,
    *,
    limit: int,
    before: tuple[datetime, uuid.UUID] | None = None,
    action_prefix: str | None = None,
    admin_user_id: uuid.UUID | None = None,
    target_id: uuid.UUID | None = None,
    since: datetime | None = None,
) -> Sequence[Row[tuple[AdminAuditLog, str | None, str | None]]]:
    """최근 순으로 한 쪽. 행마다 **주체의 login_id · 이름을 같이** 돌려줍니다.

    **LEFT JOIN 입니다.** `admin_user_id` 가 NULL 인 행이 정상으로 존재합니다 — 없는
    아이디로 두드린 로그인 실패는 가리킬 `admin_users` 행이 없습니다. INNER JOIN 으로
    바꾸면 **그 행들만 조용히 사라집니다**, 하필 제일 보고 싶은 것들이.

    **키셋 페이지네이션입니다** (`before` = 마지막으로 본 행의 `(created_at, id)`).
    OFFSET 을 안 쓰는 이유는 이 테이블이 **읽는 동안에도 늘기** 때문입니다 — 로그인마다
    행이 생기므로, OFFSET 이면 다음 쪽에서 같은 행을 다시 보거나 건너뜁니다.
    `id` 를 같이 비교하는 것은 `created_at` 이 같은 행이 있을 수 있어서입니다.

    정렬과 첫 필터는 `idx_admin_audit_log_created`(created_at DESC)와
    `idx_admin_audit_log_admin`(admin_user_id, created_at DESC)이 받습니다 (03_auth.sql).
    **`action` 에는 인덱스가 없습니다** — 접두어 필터는 created_at 으로 좁힌 뒤 걸리므로
    지금 규모에서는 문제가 없지만, 행이 크게 늘면 그때 인덱스를 답니다 (A5 와 같이 봅니다).
    """
    stmt = (
        select(AdminAuditLog, AdminUser.login_id, AdminUser.name)
        .join(AdminUser, AdminUser.id == AdminAuditLog.admin_user_id, isouter=True)
        .order_by(AdminAuditLog.created_at.desc(), AdminAuditLog.id.desc())
        .limit(limit)
    )

    if before is not None:
        at, last_id = before
        # tuple_(created_at, id) < tuple_(at, last_id) — SQL 튜플 비교로 내려갑니다.
        # 파이썬 튜플끼리 `<` 를 쓰면(예전 코드) SQLAlchemy 가 원소별로 비교하다
        # 첫 원소에서 멈춰 `created_at < at` 만 남기고, 같은 시각에 있는 행들이
        # 페이지 사이에서 조용히 사라졌습니다 — 여기서는 `tuple_()` 로 감싸
        # `(created_at, id) < (at, last_id)` 를 SQL 에 그대로 내립니다.
        stmt = stmt.where(
            tuple_(AdminAuditLog.created_at, AdminAuditLog.id) < tuple_(at, last_id)
        )
    if action_prefix is not None:
        # `admin.app_user.` 처럼 접두어로 갈래를 고릅니다.
        #
        # **`LIKE` 를 쓰지 않습니다.** action 이름에는 `_` 가 흔한데(`failed_unknown_id`)
        # 그건 LIKE 의 "아무 글자 하나" 라, 이스케이프를 빠뜨리면 엉뚱한 action 이 같이
        # 걸립니다. `starts_with` 는 메타문자가 없어 그 실수 자체가 불가능합니다.
        stmt = stmt.where(func.starts_with(AdminAuditLog.action, action_prefix))
    if admin_user_id is not None:
        stmt = stmt.where(AdminAuditLog.admin_user_id == admin_user_id)
    if target_id is not None:
        stmt = stmt.where(AdminAuditLog.target_id == target_id)
    if since is not None:
        stmt = stmt.where(AdminAuditLog.created_at >= since)

    result = await session.execute(stmt)
    return result.all()


async def retention_summary(session: AsyncSession) -> Row[Any]:
    """보존 점검용 한 줄 — 총 행 수 · 가장 오래된 행 · 가장 최근 행.

    **목록 조회와 일부러 떼어 놨습니다.** `list_entries` 가 `total` 을 안 주는 이유는
    #221 이 적어 뒀고(세는 값이 비싸고 읽는 사이에 늘어 곧 틀립니다) 그 판단은 그대로
    유효합니다 — 쪽을 넘길 때마다 전체를 세는 것은 낭비입니다. 이건 다른 질문입니다:
    **"지울 때가 됐나"** 이고, A5 가 박아 둔 기준(총 10만 행)에 닿았는지 가끔 보는 값이라
    한 번 더 세는 값이 아깝지 않습니다.

    `count(*)` 는 전체 스캔입니다. 지금은 수십~수천 행이라 무시할 수 있고, 이 값이 비싸질
    무렵이면 그것 자체가 A5 를 다시 열 신호입니다 (`docs/console/roadmap.md` §4 A5).
    """
    stmt = select(
        func.count(AdminAuditLog.id),
        func.min(AdminAuditLog.created_at),
        func.max(AdminAuditLog.created_at),
    )
    result = await session.execute(stmt)
    return result.one()
