"""로그인 · 재발급 · 로그아웃의 판단과 트랜잭션 경계.

HTTP 를 모릅니다 — 여기서 나가는 것은 예외와 TokenPair 뿐이고, 그것을 401 로 바꿀지
429 로 바꿀지는 routers/auth.py 가 정합니다.

**토큰을 만드는 법은 core/token.py, 저장하는 법은 repositories/refresh_token.py 에 있습니다.**
여기 있는 것은 "이 사람이 로그인해도 되나", "이 토큰을 아직 믿어도 되나" 하는 판단뿐입니다.

**여기 있는 login / refresh / logout 은 관리자용입니다.** 앱 회원(카카오)은
services/app_auth.py 입니다. 발급·회전·재사용 감지 규칙은 양쪽이 똑같아서
services/session.py 한 곳에 있고, 이 파일에 남은 것은 **관리자만의 판단**입니다 —
비밀번호 확인, 잠금, `status`, 해시 재계산.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.password import (
    VerifyMismatchError,
    hash_password,
    needs_rehash,
    verify_password,
)
from daengs_backend.core.subject import SubjectType
from daengs_backend.core.token import REFRESH_TTL
from daengs_backend.models import (
    AUDIT_LOGIN_DENIED_SUSPENDED,
    AUDIT_LOGIN_FAILED_PASSWORD,
    AUDIT_LOGIN_FAILED_UNKNOWN_ID,
    AUDIT_LOGIN_SUCCESS,
)
from daengs_backend.repositories import admin_user as admin_user_repo
from daengs_backend.repositories import refresh_token as refresh_token_repo
from daengs_backend.services import audit, login_attempts
from daengs_backend.services import session as session_service
from daengs_backend.services.session import (
    InvalidRefreshTokenError,
    TokenPair,
    TokenReuseDetectedError,
)

logger = logging.getLogger(__name__)

# InvalidRefreshTokenError · TokenReuseDetectedError · TokenPair 는 services/session.py
# 것을 그대로 다시 내보냅니다. 라우터가 `auth_service.X` 로 잡고 있어서, 옮겼다고
# import 를 흩어 놓으면 "어디서 잡아야 하나"가 파일마다 달라집니다.
__all__ = [
    "AuthError",
    "InvalidCredentialsError",
    "InvalidRefreshTokenError",
    "TokenPair",
    "TokenReuseDetectedError",
    "login",
    "logout",
    "refresh",
]


class AuthError(Exception):
    """인증에 실패했습니다. 아래 것들의 부모입니다."""


class InvalidCredentialsError(AuthError):
    """아이디가 없거나, 비밀번호가 틀렸거나, 계정이 정지되었습니다.

    **셋을 하나로 합친 것은 일부러입니다.** 나눠서 응답하면 어느 아이디가 존재하는지가
    새어 나갑니다. 특히 정지된 계정에 "정지됨"을 돌려주면 비밀번호를 맞혔다는 사실까지
    알려 주게 됩니다. 무엇이 문제였는지는 서버 로그에만 남깁니다.
    """


def _attempt_detail(login_id: str) -> dict[str, str]:
    """감사 행에 남길 "무엇으로 시도했나".

    없는 아이디로 두드린 실패는 `admin_user_id` 가 NULL 이라, 이 값이 없으면 그 행에
    아무 단서도 남지 않습니다.

    **50자로 자르는 이유**는 로그인 칸에 아무 문자열이나 들어올 수 있어서입니다 —
    아이디 칸에 이메일을 잘못 치는 것 같은 경우요. `admin_users.login_id` 와 같은
    길이로 묶어, 감사 테이블이 남의 긴 입력을 그대로 보관하지 않게 합니다.
    """
    return {"login_id": login_id[:50]}


async def login(
    session: AsyncSession,
    *,
    login_id: str,
    password: str,
    user_agent: str | None = None,
    ip: str,
) -> TokenPair:
    """아이디·비밀번호를 확인하고 토큰 한 쌍을 발급합니다.

    잠금 확인이 **계정 조회보다 먼저**입니다. 없는 아이디로 두드릴 때도 똑같이
    잠겨야 하기 때문입니다 — 순서가 바뀌면 잠기는지 여부로 계정 존재가 드러납니다.
    """
    login_attempts.check_not_locked(login_id, ip)

    admin = await admin_user_repo.get_by_login_id(session, login_id)
    if admin is None:
        # 비밀번호가 틀렸을 때와 똑같이 셉니다. 한쪽만 세면 차이가 드러납니다.
        login_attempts.record_failure(login_id, ip)
        logger.info("로그인 실패: 없는 아이디 (ip=%s)", ip)
        # **주체가 없는 감사 행입니다** — 가리킬 admin_users 행이 없어서
        # admin_user_id 가 NULL 로 남고, 무엇을 시도했는지는 detail 에만 있습니다.
        await audit.record_and_commit(
            session,
            action=AUDIT_LOGIN_FAILED_UNKNOWN_ID,
            detail=_attempt_detail(login_id),
            ip=ip,
        )
        raise InvalidCredentialsError

    try:
        verify_password(admin.password_hash, password)
    except VerifyMismatchError:
        login_attempts.record_failure(login_id, ip)
        logger.info("로그인 실패: 비밀번호 불일치 (admin=%s, ip=%s)", admin.id, ip)
        await audit.record_and_commit(
            session,
            action=AUDIT_LOGIN_FAILED_PASSWORD,
            admin_user_id=admin.id,
            detail=_attempt_detail(login_id),
            ip=ip,
        )
        raise InvalidCredentialsError from None
    # InvalidHashError 는 잡지 않습니다. 저장된 해시가 깨졌다는 뜻이고,
    # 그건 401 이 아니라 데이터 사고라 500 으로 올라가야 합니다 (core/password.py).

    if admin.status != "active":
        # 비밀번호는 맞았지만 들여보내지 않습니다.
        # 실패로 세지 않는 이유는 추측 시도가 아니기 때문입니다.
        # 응답은 아이디·비밀번호가 틀렸을 때와 똑같습니다 — 여기서 "정지됨"을 알려 주면
        # 비밀번호를 맞혔다는 사실까지 알려 주게 됩니다.
        logger.warning("로그인 거부: 정지된 계정 (admin=%s, ip=%s)", admin.id, ip)
        # 실패로 세지는 않지만 **기록은 남깁니다.** 정지된 계정에 맞는 비밀번호가
        # 들어왔다는 것은 잠금 카운터보다 감사 쪽에서 값이 큰 사건입니다.
        await audit.record_and_commit(
            session,
            action=AUDIT_LOGIN_DENIED_SUSPENDED,
            admin_user_id=admin.id,
            detail=_attempt_detail(login_id),
            ip=ip,
        )
        raise InvalidCredentialsError

    now = datetime.now(UTC)
    login_attempts.record_success(login_id, ip)
    admin.last_login_at = now

    # 로그인은 비밀번호 원문을 아는 유일한 순간이라, 파라미터가 올라갔으면
    # 지금이 다시 해시할 수 있는 유일한 기회입니다 (core/password.py).
    if needs_rehash(admin.password_hash):
        admin.password_hash = hash_password(password)
        logger.info("비밀번호 해시를 새 파라미터로 갱신 (admin=%s)", admin.id)

    pair = await session_service.issue(
        session,
        subject_type=SubjectType.ADMIN,
        subject_id=admin.id,
        role=admin.role,
        refresh_expires_at=now + REFRESH_TTL,
        user_agent=user_agent,
        ip=ip,
    )
    # 성공 경로는 `record_and_commit` 이 아닙니다 — 아래 commit 하나가 토큰 발급 ·
    # last_login_at · 이 기록을 함께 확정합니다. 토큰은 나갔는데 기록만 없는 상태를
    # 만들지 않으려는 것입니다.
    await audit.record(
        session,
        action=AUDIT_LOGIN_SUCCESS,
        admin_user_id=admin.id,
        detail=_attempt_detail(login_id),
        ip=ip,
    )
    await session.commit()
    logger.info("로그인 성공 (admin=%s, ip=%s)", admin.id, ip)
    return pair


async def refresh(
    session: AsyncSession,
    *,
    refresh_token: str,
    user_agent: str | None = None,
    ip: str | None = None,
) -> TokenPair:
    """refresh 를 새 한 쌍으로 바꿉니다 (회전). 쓴 토큰은 즉시 폐기됩니다.

    토큰 자체를 믿어도 되는지는 services/session.py 가 봅니다 (만료 · 폐기 · 재사용
    감지 · **주체가 관리자인지**). 여기서 이어서 하는 것은 **계정 확인**입니다 —
    토큰이 멀쩡해도 그 사이 계정이 정지·삭제되었을 수 있습니다.
    """
    row = await session_service.take_for_rotation(
        session,
        refresh_token=refresh_token,
        subject_type=SubjectType.ADMIN,
        ip=ip,
    )

    admin = await admin_user_repo.get_by_id(session, row.subject_id)
    if admin is None or admin.status != "active":
        # 계정이 사라졌거나 정지되었습니다. 남은 세션을 여기서 정리합니다 —
        # status 는 '새 로그인'을 막을 뿐이라, 이미 나간 세션은 이렇게 끊어야 합니다.
        await session_service.drop_all(session, SubjectType.ADMIN, row.subject_id)
        await session.commit()
        logger.warning("재발급 거부: 쓸 수 없는 계정 (admin=%s)", row.subject_id)
        raise InvalidRefreshTokenError

    pair = await session_service.issue(
        session,
        subject_type=SubjectType.ADMIN,
        subject_id=admin.id,
        role=admin.role,
        # **연장하지 않습니다.** 옛 행의 만료를 그대로 물려받아, 계속 쓰더라도
        # 처음 로그인한 시각 기준 REFRESH_TTL 이 지나면 다시 로그인해야 합니다.
        refresh_expires_at=row.expires_at,
        user_agent=user_agent,
        ip=ip,
    )
    await session.commit()
    return pair


async def logout(session: AsyncSession, *, refresh_token: str) -> None:
    """세션 하나를 끊습니다. 없는 토큰이어도 조용히 성공합니다.

    없을 때 404 를 주면 "이 토큰은 있었다/없었다"를 알려 주게 되고, 무엇보다
    이미 로그아웃된 상태에서 다시 부르는 것은 오류가 아닙니다.

    **행을 지웁니다 (revoke 가 아닙니다).** revoked_at 을 찍으면 재사용 감지의
    유예 창을 타고 10초 동안 통과해 버립니다 (repositories/refresh_token.py).
    """
    row = await session_service.find(session, refresh_token)
    if row is None:
        return

    subject_type, subject_id = row.subject_type, row.subject_id
    await refresh_token_repo.delete_one(session, row)
    await session.commit()
    logger.info("로그아웃 (%s=%s)", subject_type.value, subject_id)
