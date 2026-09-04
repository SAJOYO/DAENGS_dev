"""관리자 계정 발급 · 정지 · 권한 변경의 판단과 트랜잭션 경계 (콘솔 로드맵 A3).

HTTP 를 모릅니다 — 나가는 것은 예외와 `AdminUser` 뿐이고, 그것을 409 로 바꿀지
422 로 바꿀지는 `routers/admin_account.py` 가 정합니다.

`services/auth.py` 와 나란한 자리입니다. 저쪽이 "이 사람이 로그인해도 되나"라면
여기는 **"이 계정이 존재해도 되나"** 입니다. 비밀번호 해시는 양쪽 다
`core/password.py` 한 곳을 씁니다 (D-012 — 개인정보 암복호화의 `core/crypto.py`
와 바꿔 쓰지 마세요).

--------------------------------------------------------------------------------
**스스로를 잠그지 않게 하는 것이 이 파일에서 제일 중요합니다.**

관리자 계정을 다루는 API 는 자기 발등을 찍을 수 있는 유일한 자리입니다. 마지막 ADMIN
이 정지되거나 VIEWER 로 내려가면, 그것을 되돌릴 수 있는 사람이 아무도 남지 않습니다 —
복구는 서버에 붙어 psql 로 UPDATE 하는 것뿐이고 (`docs/collaboration.md` §7 "상태는
사람이 안다"), 그건 GCP 운영 DB 에서는 SSH 터널부터 여는 일입니다.

그래서 가드가 **둘**입니다.

  ① 자기 자신의 role·status 는 못 바꾼다
  ② 마지막 active ADMIN 은 정지도 강등도 못 한다

①만으로 충분해 보이지만 아닙니다. access token 은 무상태라 role 이 최대 5분 낡을 수
있어서(`core/deps.py` 의 `Principal`), 방금 강등된 사람이 5분 동안 ADMIN 토큰을 그대로
씁니다. ADMIN 둘이 서로를 강등하면 ①을 어기지 않고도 ADMIN 이 0이 됩니다.
--------------------------------------------------------------------------------
"""

import logging
import uuid
from collections.abc import Sequence

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.password import hash_password
from daengs_backend.core.subject import SubjectType
from daengs_backend.models import (
    AUDIT_ACCOUNT_CREATED,
    AUDIT_ACCOUNT_REACTIVATED,
    AUDIT_ACCOUNT_ROLE_CHANGED,
    AUDIT_ACCOUNT_SUSPENDED,
    AdminUser,
)
from daengs_backend.repositories import admin_user as admin_user_repo
from daengs_backend.services import audit
from daengs_backend.services import session as session_service

logger = logging.getLogger(__name__)

__all__ = [
    "AdminAccountError",
    "AdminNotFoundError",
    "LastAdminError",
    "LoginIdTakenError",
    "SelfChangeError",
    "create_account",
    "list_accounts",
    "update_account",
]

#: 이 role 이 0명이 되면 아무도 계정 관리를 못 합니다 (`ROLE_PERMISSIONS`).
_ADMIN_ROLE = "ADMIN"


class AdminAccountError(Exception):
    """계정 관리 요청을 받아들일 수 없습니다. 아래 것들의 부모입니다."""


class LoginIdTakenError(AdminAccountError):
    """그 `login_id` 를 이미 누가 씁니다.

    **로그인과 달리 여기서는 존재를 알려 줍니다.** 로그인에서 아이디 존재를 숨기는
    것은 바깥 사람이 두드리기 때문이고, 여기는 ADMIN 만 들어오는 문이라 숨길 상대가
    없습니다. 숨기면 "왜 안 만들어지는지" 모르는 화면이 됩니다.
    """


class AdminNotFoundError(AdminAccountError):
    """그 id 의 계정이 없습니다."""


class SelfChangeError(AdminAccountError):
    """자기 자신의 role·status 는 못 바꿉니다 (파일 docstring 의 가드 ①)."""


class LastAdminError(AdminAccountError):
    """마지막 active ADMIN 입니다 (가드 ②). 정지도 강등도 막습니다."""


async def list_accounts(session: AsyncSession) -> Sequence[AdminUser]:
    """전부. 정지된 계정도 포함합니다 — 화면에서 안 보이면 해제할 수 없습니다."""
    return await admin_user_repo.list_all(session)


async def create_account(
    session: AsyncSession,
    *,
    actor_id: uuid.UUID,
    login_id: str,
    password: str,
    name: str,
    role: str,
    ip: str | None = None,
) -> AdminUser:
    """계정을 발급합니다. 초기 비밀번호는 **발급자가 정해 사람에게 전달합니다.**

    메일로 보내거나 임시 비밀번호를 만들지 않습니다 — 관리자는 가입이 아니라 발급이라
    이메일 인증 절차 자체가 없고(`db/init/03_auth.sql`), 팀 채널로 건네는 것이 지금의
    실제 경로입니다 (`uv run seed-admin` 과 같습니다).

    받은 비밀번호는 **여기서 즉시 해시하고 원문을 어디에도 남기지 않습니다.**
    로그에도 감사 `detail` 에도 넣지 마세요.
    """
    try:
        # **`create` 가 try 안에 있어야 합니다.** `login_id` 중복을 실제로 막는 것은
        # `admin_users_login_id_key` UNIQUE 뿐이고, 그 위반은 repo 의 flush 에서
        # 올라옵니다 (repositories/admin_user.py). 밖에 두면 500 이 나갑니다.
        admin = await admin_user_repo.create(
            session,
            login_id=login_id,
            password_hash=hash_password(password),
            name=name,
            role=role,
        )
        await audit.record(
            session,
            action=AUDIT_ACCOUNT_CREATED,
            admin_user_id=actor_id,
            target_type="admin_user",
            target_id=admin.id,
            # **비밀번호는 넣지 않습니다.** 남기는 것은 "무엇을 만들었나" 까지입니다.
            detail={"login_id": login_id, "role": role},
            ip=ip,
        )
        # 계정과 그 기록을 한 번에 확정합니다. 계정만 생기고 기록이 없는 상태를
        # 만들지 않으려는 것입니다 (`services/audit.py` 의 성공 경로).
        await session.commit()
    except IntegrityError:
        await session.rollback()
        logger.info("계정 발급 거부: 중복 login_id (%s)", login_id)
        raise LoginIdTakenError from None

    logger.info(
        "계정 발급 (actor=%s, admin=%s, role=%s)", actor_id, admin.id, admin.role
    )
    return admin


async def update_account(
    session: AsyncSession,
    *,
    actor_id: uuid.UUID,
    target_id: uuid.UUID,
    role: str | None = None,
    status: str | None = None,
    ip: str | None = None,
) -> AdminUser:
    """role·status 를 바꿉니다. `None` 인 것은 건드리지 않습니다.

    **바뀐 것마다 감사 행을 따로 남깁니다.** 한 요청에서 role 과 status 를 같이
    바꾸면 두 줄이 됩니다 — "누가 정지시켰나"를 세는 것이 파싱이 아니라 집계로
    남게 하려는 것입니다 (`schemas/admin_account.py`).

    정지에서 **세션까지 끊는 이유**는 `status` 가 새 로그인만 막기 때문입니다
    (03_auth.sql 의 status 주석 "둘 다 필요하다"). 다만 access token 은 무상태라
    **최대 ACCESS_TTL(5분) 동안은 정지된 계정도 API 를 씁니다** — 그걸 없애려면
    요청마다 DB 를 봐야 하고, 그러지 않기로 한 것이 D-015 입니다.

    role 변경에서는 세션을 안 끊습니다. `services/auth.py` 의 `refresh` 가 DB 의
    role 로 새 토큰을 굽기 때문에 ≤5분이면 저절로 반영되고, 끊어 봐야 지금 손에 있는
    access token 5분은 똑같이 남습니다 — 얻는 것 없이 로그인만 다시 시킵니다.
    """
    target = await admin_user_repo.get_by_id(session, target_id)
    if target is None:
        raise AdminNotFoundError

    # 가드 ① — 무엇을 바꾸든 자기 자신이면 막습니다. "어차피 같은 값이면 괜찮다"로
    # 예외를 두지 않습니다. 그 예외가 곧 `role: "ADMIN"` 을 자기에게 주는 길입니다.
    if target.id == actor_id and (role is not None or status is not None):
        raise SelfChangeError

    demoting = role is not None and role != target.role and target.role == _ADMIN_ROLE
    suspending = status == "suspended" and target.status == "active"

    # 가드 ② — 파일 docstring 의 5분 창 때문에 ①만으로는 새지 않는다고 말할 수 없습니다.
    if target.role == _ADMIN_ROLE and target.status == "active" and (demoting or suspending):
        if await admin_user_repo.count_active_with_role(session, _ADMIN_ROLE) <= 1:
            logger.warning(
                "계정 변경 거부: 마지막 ADMIN (actor=%s, target=%s)", actor_id, target_id
            )
            raise LastAdminError

    if role is not None and role != target.role:
        before = target.role
        target.role = role
        await audit.record(
            session,
            action=AUDIT_ACCOUNT_ROLE_CHANGED,
            admin_user_id=actor_id,
            target_type="admin_user",
            target_id=target.id,
            detail={"from": before, "to": role},
            ip=ip,
        )
        logger.info(
            "role 변경 (actor=%s, target=%s, %s→%s)", actor_id, target_id, before, role
        )

    if status is not None and status != target.status:
        target.status = status
        if status == "suspended":
            dropped = await session_service.drop_all(
                session, SubjectType.ADMIN, target.id
            )
            await audit.record(
                session,
                action=AUDIT_ACCOUNT_SUSPENDED,
                admin_user_id=actor_id,
                target_type="admin_user",
                target_id=target.id,
                # 끊은 세션 수를 같이 남깁니다. 0이면 "이미 안 쓰고 있던 계정",
                # 여럿이면 "쓰던 사람을 끊었다"라 사후에 의미가 다릅니다.
                detail={"sessions_dropped": dropped},
                ip=ip,
            )
            logger.warning(
                "계정 정지 (actor=%s, target=%s, 세션 %d개 끊음)",
                actor_id,
                target_id,
                dropped,
            )
        else:
            await audit.record(
                session,
                action=AUDIT_ACCOUNT_REACTIVATED,
                admin_user_id=actor_id,
                target_type="admin_user",
                target_id=target.id,
                ip=ip,
            )
            logger.info("계정 정지 해제 (actor=%s, target=%s)", actor_id, target_id)

    # 바뀐 것이 없어도 커밋합니다 — 아무것도 안 걸려 있으면 빈 커밋이라 값이 같습니다.
    await session.commit()
    return target
