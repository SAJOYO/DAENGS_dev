"""refresh_tokens 쿼리. 판단은 없습니다.

**이 파일의 불변식**: `revoked_at` 이 찍힌 행은 **회전으로 교체된 것뿐입니다.**

    회전            revoke()      revoked_at 을 찍고 행은 남깁니다
    로그아웃        delete()      행을 지웁니다
    강제 로그아웃   delete_all_for_admin()

로그아웃이 행을 남기지 않는 이유는 재사용 감지의 유예 창 때문입니다.
services/auth.py 는 "폐기된 지 얼마 안 된 토큰"을 탭 경합으로 보고 통과시키는데,
로그아웃한 토큰까지 그 규칙을 타면 **로그아웃 뒤 10초 동안 문이 열립니다.**
사유 컬럼을 더하는 대신 폐기 방식을 나눠서, `revoked_at` 이 있다는 것 자체가
"회전으로 교체됨"을 뜻하게 만듭니다.

대량 폐기도 같은 이유로 지웁니다. revoked_at 으로 하면 다른 세션들이 10초 안에
재발급을 시도할 때 유예 창을 타고 새 토큰을 받아 갑니다 — 끊으려던 것이 안 끊깁니다.

commit 은 하지 않습니다. 트랜잭션 경계는 services 가 잡습니다.
"""

import uuid
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import RefreshToken

__all__ = [
    "create",
    "delete_all_for_admin",
    "delete_one",
    "get_by_hash",
    "revoke",
]


async def create(
    session: AsyncSession,
    *,
    admin_user_id: uuid.UUID,
    token_hash: str,
    expires_at: datetime,
    user_agent: str | None = None,
    ip: str | None = None,
) -> RefreshToken:
    """세션 한 줄을 만듭니다. 로그인과 회전이 둘 다 씁니다.

    받는 것은 **원문이 아니라 해시**입니다. 원문은 쿠키로만 나가고 어디에도 남지 않습니다.
    user_agent / ip 는 세션 목록 화면에 보여 줄 표시용입니다 —
    클라이언트가 바꿀 수 있는 값이라 인증 판단에 쓰면 안 됩니다.
    """
    token = RefreshToken(
        admin_user_id=admin_user_id,
        token_hash=token_hash,
        expires_at=expires_at,
        user_agent=user_agent,
        ip=ip,
    )
    session.add(token)
    # id 와 created_at 은 DB 가 채웁니다. 회전할 때 방금 만든 행을 바로 써야 해서
    # 여기서 flush 해 둡니다 (commit 은 아닙니다 — 롤백은 그대로 됩니다).
    await session.flush()
    return token


async def get_by_hash(session: AsyncSession, token_hash: str) -> RefreshToken | None:
    """해시로 한 줄. 재발급의 첫 단계입니다.

    **폐기됐는지 만료됐는지 여기서 거르지 않습니다.** 있는 그대로 돌려줍니다 —
    "폐기된 토큰이 다시 왔다"는 것 자체가 services 가 봐야 할 신호라,
    여기서 None 으로 뭉개면 탈취를 감지할 수 없습니다.
    """
    stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    return await session.scalar(stmt)


async def revoke(session: AsyncSession, token: RefreshToken, at: datetime) -> None:
    """회전으로 교체됐다고 표시합니다. **회전 말고 다른 데 쓰지 마세요.**

    행을 남기는 이유는 이 토큰이 다시 들어왔을 때 "모르는 토큰"이 아니라
    "방금 교체된 토큰"으로 알아보기 위해서입니다. 그래야 탈취를 감지합니다.
    """
    token.revoked_at = at


async def delete_one(session: AsyncSession, token: RefreshToken) -> None:
    """세션 하나를 없앱니다. 로그아웃용입니다.

    revoke() 가 아닌 이유는 위 불변식 때문입니다 — 로그아웃한 토큰에
    revoked_at 을 찍으면 유예 창을 타고 통과해 버립니다.
    """
    await session.delete(token)


async def delete_all_for_admin(
    session: AsyncSession, admin_user_id: uuid.UUID
) -> int:
    """그 계정의 세션을 전부 없앱니다. 지운 행 수를 돌려줍니다.

    쓰는 곳은 둘입니다 — 탈취가 의심될 때(재사용 감지)와 관리자가 강제로 끊을 때.
    계정을 공유하고 있으므로 **팀 전원이 다시 로그인하게 됩니다.** 토큰이 털렸다면
    그 계정 전체가 위험한 것이라 이게 맞는 대응입니다.

    idx_refresh_tokens_admin 인덱스가 이 쿼리를 위한 것입니다 (03_auth.sql).
    """
    stmt = delete(RefreshToken).where(RefreshToken.admin_user_id == admin_user_id)
    result = await session.execute(stmt)
    return result.rowcount
