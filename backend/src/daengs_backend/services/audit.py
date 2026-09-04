"""관리자 행위를 감사 기록에 남깁니다.

**이것은 로그가 아니라 데이터입니다.** `logger.info` 와 나란히 부르게 되지만 목적이
다릅니다 — 로그는 "무슨 일이 있었나"를 사람이 읽으려고 남기는 것이고, 여기 남는 것은
나중에 **따져야 하는 행위**입니다 (개인정보 복호화 조회 · 계정 정지 · 신고 처리).
운영 로그를 이 테이블에 넣지 마세요. 그 선은 `docs/console/roadmap.md` §6 에 있습니다.

--------------------------------------------------------------------------------
**커밋 경계 — 이 파일에서 제일 틀리기 쉬운 자리입니다.**

이 프로젝트의 규칙은 "commit 은 services 가 한다"이고, 감사 기록도 보통은 그 규칙을
그대로 따릅니다 (`record`). 그런데 **실패를 기록할 때는 그 규칙이 정반대로 작동합니다.**

    login_attempts.record_failure(...)
    await audit.record(session, action=...)   # 세션에 얹기만 했다
    raise InvalidCredentialsError             # 이 예외로 세션이 롤백된다

`get_session`(core/database.py)은 커밋하지 않은 변경을 그대로 버리고 닫습니다. 그래서
위 코드는 **에러가 하나도 안 나면서** 아무것도 안 남깁니다 — "기록이 안 남는 감사 로그"가
됩니다. 실패 경로에서는 예외를 던지기 **전에** 확정해야 하고, 그것이 `record_and_commit`
입니다.

성공 경로는 반대입니다. 거기서는 `record` 를 쓰고 호출한 services 가 나머지 변경과 함께
한 번에 커밋합니다 — 토큰은 발급됐는데 기록만 없거나, 그 반대인 상태가 생기지 않습니다.
--------------------------------------------------------------------------------
"""

import base64
import binascii
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import AdminAuditLog
from daengs_backend.repositories import admin_audit_log as audit_repo

__all__ = [
    "AuditEntry",
    "AuditPage",
    "InvalidCursorError",
    "list_entries",
    "record",
    "record_and_commit",
]


async def record(
    session: AsyncSession,
    *,
    action: str,
    admin_user_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    detail: dict[str, Any] | None = None,
    request_id: str | None = None,
    ip: str | None = None,
) -> None:
    """감사 행을 **호출자의 트랜잭션에 얹습니다.** 커밋은 호출한 services 가 합니다.

    성공 경로용입니다 — 한 일과 그 기록이 같이 남거나 같이 없거나여야 할 때 씁니다.
    예외를 던지며 끝나는 경로에서 이것을 쓰면 기록이 롤백에 쓸려 나갑니다
    (위 파일 docstring). 그때는 `record_and_commit` 입니다.
    """
    await audit_repo.add(
        session,
        action=action,
        admin_user_id=admin_user_id,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
        request_id=request_id,
        ip=ip,
    )


async def record_and_commit(
    session: AsyncSession,
    *,
    action: str,
    admin_user_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    detail: dict[str, Any] | None = None,
    request_id: str | None = None,
    ip: str | None = None,
) -> None:
    """감사 행을 **그 자리에서 확정합니다.** 예외를 던지며 끝나는 경로용입니다.

    ⚠ **세션에 걸려 있는 다른 변경까지 함께 커밋합니다.** 부르기 전에 그 경로에
    확정하면 안 되는 변경이 없는지 확인하세요. 지금 쓰는 자리(로그인 실패)는 계정을
    읽기만 하고 아무것도 고치지 않아서 안전합니다.

    **기록에 실패하면 예외를 삼키지 않고 그대로 올립니다.** 조용히 버리면 "남았을
    것"과 "안 남은 것"이 구분되지 않아, 감사 로그가 있는 것이 없는 것보다 나쁜
    상태가 됩니다. 여기까지 왔다는 것은 DB 조회가 이미 성공했다는 뜻이라, 이 자리의
    실패는 401 로 가릴 일이 아니라 드러나야 하는 사고입니다.
    """
    await record(
        session,
        action=action,
        admin_user_id=admin_user_id,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
        request_id=request_id,
        ip=ip,
    )
    await session.commit()


# --------------------------------------------------------------------------
# 읽는 쪽 (#221). 위쪽 쓰기와 달리 트랜잭션 경계가 없습니다 — 조회뿐입니다.
#
# **감사 로그를 본 것은 감사에 남기지 않습니다.** 남기면 이 화면을 열 때마다 행이
# 쌓여 같은 화면이 자기 기록으로 채워지고, 그 행을 본 것도 남겨야 하는 재귀가 됩니다.
# 로드맵 §6 의 "감사 로그는 로그가 아니라 데이터" 와 같은 선입니다.
# --------------------------------------------------------------------------

#: 한 번에 주는 최대 행 수. 화면의 "더 보기" 가 이 단위로 부릅니다.
MAX_LIMIT = 200
DEFAULT_LIMIT = 50


class InvalidCursorError(Exception):
    """커서가 우리가 만든 값이 아닙니다. 라우터가 422 로 바꿉니다."""


@dataclass(frozen=True)
class AuditEntry:
    """감사 행 + 주체 이름. 라우터가 스키마로 옮깁니다."""

    entry: AdminAuditLog
    actor_login_id: str | None
    actor_name: str | None


@dataclass(frozen=True)
class AuditPage:
    entries: list[AuditEntry]
    next_cursor: str | None


def _encode_cursor(at: datetime, entry_id: uuid.UUID) -> str:
    """`(created_at, id)` 를 불투명한 문자열로.

    **불투명하게 두는 이유**는 다음 쪽을 부르는 방법이 우리 계약이지 클라이언트가
    조립할 것이 아니기 때문입니다. 화면은 받은 값을 그대로 돌려주기만 하면 되고,
    나중에 정렬 기준을 바꿔도 화면을 안 고칩니다.
    """
    raw = f"{at.isoformat()}|{entry_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """커서를 되돌립니다. 우리가 만든 값이 아니면 `InvalidCursorError`.

    **500 으로 새지 않게 여기서 잡습니다.** 커서는 URL 에 그대로 실려 오므로 사람이
    손으로 고친 값이 들어올 수 있고, 그건 서버 잘못이 아니라 잘못된 요청입니다.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        at_text, _, id_text = raw.partition("|")
        return datetime.fromisoformat(at_text), uuid.UUID(id_text)
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise InvalidCursorError from None


async def list_entries(
    session: AsyncSession,
    *,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
    action_prefix: str | None = None,
    admin_user_id: uuid.UUID | None = None,
    target_id: uuid.UUID | None = None,
    since: datetime | None = None,
) -> AuditPage:
    """최근 순 한 쪽 + 다음 커서.

    **`limit + 1` 개를 읽어 다음 쪽이 있는지 봅니다.** 정확히 `limit` 개를 읽으면
    "이게 마지막인가"를 알 수 없어 화면이 빈 쪽을 한 번 더 부르게 됩니다.
    """
    capped = max(1, min(limit, MAX_LIMIT))
    before = _decode_cursor(cursor) if cursor else None

    rows = await audit_repo.list_entries(
        session,
        limit=capped + 1,
        before=before,
        action_prefix=action_prefix,
        admin_user_id=admin_user_id,
        target_id=target_id,
        since=since,
    )

    has_more = len(rows) > capped
    kept = rows[:capped]
    entries = [
        AuditEntry(entry=row[0], actor_login_id=row[1], actor_name=row[2])
        for row in kept
    ]
    next_cursor = (
        _encode_cursor(entries[-1].entry.created_at, entries[-1].entry.id)
        if has_more and entries
        else None
    )
    return AuditPage(entries=entries, next_cursor=next_cursor)
