"""admin_users 조회. 쿼리만 있고 판단은 없습니다.

"이 사람이 로그인해도 되나"(status 확인, 비밀번호 검증)는 services 계층이 합니다.
여기서는 `status` 로 거르지 않고 있는 그대로 돌려줍니다 -
정지된 계정도 관리 화면에서는 보여야 하기 때문입니다.

commit 은 하지 않습니다. 트랜잭션 경계는 services 가 잡습니다.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.models import AdminUser


async def get_by_id(session: AsyncSession, admin_id: uuid.UUID) -> AdminUser | None:
    """PK 로 한 명. 토큰의 subject 를 계정으로 되돌릴 때 씁니다."""
    return await session.get(AdminUser, admin_id)


async def get_by_login_id(session: AsyncSession, login_id: str) -> AdminUser | None:
    """로그인 아이디로 한 명. 로그인 첫 단계입니다.

    없으면 None 입니다. 호출하는 쪽에서 "아이디가 없음"과 "비밀번호가 틀림"을
    **다른 응답으로 구분하지 마세요** - 계정 존재 여부가 새어 나갑니다.
    """
    stmt = select(AdminUser).where(AdminUser.login_id == login_id)
    return await session.scalar(stmt)


async def list_by_role(session: AsyncSession, role: str) -> Sequence[AdminUser]:
    """권한별 목록. 정렬은 login_id 순입니다.

    DB collation 이 `C` 라 영문 대소문자가 섞이면 대문자가 먼저 옵니다 (D-008).
    관리자 아이디는 소문자 규칙이라 지금은 문제가 없지만, 화면 정렬이
    어색해지면 `ORDER BY login_id COLLATE "ko-KR-x-icu"` 를 붙이세요.
    """
    stmt = select(AdminUser).where(AdminUser.role == role).order_by(AdminUser.login_id)
    result = await session.scalars(stmt)
    return result.all()
