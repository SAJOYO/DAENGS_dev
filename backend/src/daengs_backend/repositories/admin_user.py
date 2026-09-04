"""admin_users 조회. 쿼리만 있고 판단은 없습니다.

"이 사람이 로그인해도 되나"(status 확인, 비밀번호 검증)는 services 계층이 합니다.
여기서는 `status` 로 거르지 않고 있는 그대로 돌려줍니다 -
정지된 계정도 관리 화면에서는 보여야 하기 때문입니다.

commit 은 하지 않습니다. 트랜잭션 경계는 services 가 잡습니다.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
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


async def list_all(session: AsyncSession) -> Sequence[AdminUser]:
    """전부. 계정 관리 화면이 읽습니다 (`GET /admin/admins`).

    `status` 로 거르지 않는 것은 이 파일 첫 문단 그대로입니다 — 정지된 계정을
    화면에서 못 보면 해제할 방법도 없습니다.

    페이지네이션이 없습니다. `admin_users` 는 팀 인원 수만큼이라 늘어날 이유가
    없습니다. 앱 회원(A2)은 사정이 다르므로 그쪽은 그때 따로 답니다.
    """
    stmt = select(AdminUser).order_by(AdminUser.login_id)
    result = await session.scalars(stmt)
    return result.all()


async def create(
    session: AsyncSession,
    *,
    login_id: str,
    password_hash: str,
    name: str,
    role: str,
) -> AdminUser:
    """계정 한 줄을 세션에 얹고 flush 합니다. commit 은 services 가 합니다.

    **`login_id` 중복은 여기서 확인하지 않습니다.** 미리 SELECT 해서 봐도 그 사이
    다른 요청이 같은 아이디를 넣을 수 있어, 진짜로 막는 것은 `admin_users_login_id_key`
    UNIQUE 하나뿐입니다. flush 가 그 위반을 **이 자리에서** IntegrityError 로 드러내고,
    services 가 그것을 도메인 예외로 바꿉니다.

    `status` 는 넣지 않습니다 — DB DEFAULT 'active' 가 채웁니다 (03_auth.sql).
    """
    admin = AdminUser(
        login_id=login_id,
        password_hash=password_hash,
        name=name,
        role=role,
    )
    session.add(admin)
    await session.flush()
    return admin


async def count_active_with_role(session: AsyncSession, role: str) -> int:
    """그 role 이면서 active 인 계정 수.

    "마지막 ADMIN 을 정지·강등하지 못하게" 를 판정하는 데만 씁니다. 여기서는 세기만
    하고, 몇이면 막을지는 services 가 정합니다.
    """
    stmt = (
        select(func.count())
        .select_from(AdminUser)
        .where(AdminUser.role == role, AdminUser.status == "active")
    )
    return await session.scalar(stmt) or 0
