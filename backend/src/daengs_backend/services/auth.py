"""로그인 · 재발급 · 로그아웃의 판단과 트랜잭션 경계.

HTTP 를 모릅니다 — 여기서 나가는 것은 예외와 TokenPair 뿐이고, 그것을 401 로 바꿀지
429 로 바꿀지는 routers/auth.py 가 정합니다.

**토큰을 만드는 법은 core/token.py, 저장하는 법은 repositories/refresh_token.py 에 있습니다.**
여기 있는 것은 "이 사람이 로그인해도 되나", "이 토큰을 아직 믿어도 되나" 하는 판단뿐입니다.

**여기 있는 login / refresh / logout 은 관리자용입니다.** 앱 회원(카카오)의 로그인은
같은 회전 규칙을 쓰지만 자격 확인 방법이 달라서 별도 서비스로 갑니다 —
공유되는 것은 아래 `_issue` 와 `refresh` 의 회전·재사용 감지입니다 (D-016).
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.password import (
    VerifyMismatchError,
    hash_password,
    needs_rehash,
    verify_password,
)
from daengs_backend.core.subject import SubjectType
from daengs_backend.core.token import (
    ACCESS_TTL,
    REFRESH_REUSE_GRACE,
    REFRESH_TTL,
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
)
from daengs_backend.models import AdminUser
from daengs_backend.repositories import admin_user as admin_user_repo
from daengs_backend.repositories import refresh_token as refresh_token_repo
from daengs_backend.services import login_attempts

logger = logging.getLogger(__name__)

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


class InvalidRefreshTokenError(AuthError):
    """모르는 토큰이거나, 로그아웃했거나, 만료되었습니다. 조용히 401 입니다."""


class TokenReuseDetectedError(AuthError):
    """**이미 회전으로 교체된 토큰이 유예 시간을 한참 지나 다시 들어왔습니다.**

    정상 사용자는 새 토큰을 받아 갔으므로 옛 것을 다시 쓸 이유가 없습니다.
    탈취를 의심하고 그 계정의 세션을 **전부** 끊은 뒤 이 예외를 냅니다.
    계정을 공유하고 있으므로 팀 전원이 다시 로그인하게 되는데, 토큰이 털렸다면
    그 계정 전체가 위험한 것이라 이게 맞는 대응입니다.
    """


@dataclass(frozen=True)
class TokenPair:
    """새로 발급한 토큰 한 쌍과, 쿠키를 굽는 데 필요한 시각들."""

    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime
    subject_type: SubjectType
    subject_id: uuid.UUID
    #: 관리자만 가집니다. 앱 회원은 None 입니다.
    role: str | None


async def _issue(
    session: AsyncSession,
    *,
    subject_type: SubjectType,
    subject_id: uuid.UUID,
    role: str | None,
    refresh_expires_at: datetime,
    user_agent: str | None,
    ip: str | None,
) -> TokenPair:
    """토큰 한 쌍을 만들고 refresh 를 DB 에 남깁니다. commit 은 부르는 쪽이 합니다.

    **주체 종류를 가리지 않습니다.** 앱 회원 로그인도 이 함수를 씁니다 — 회전과
    재사용 감지 규칙(D-015)을 한 벌로 유지하려는 것이 D-016 의 요지입니다.

    refresh 의 만료를 인자로 받는 이유는 **회전할 때 연장하지 않기 위해서**입니다.
    로그인은 now + REFRESH_TTL 을 넘기고, 회전은 옛 행의 expires_at 을 그대로 넘깁니다.
    여기서 계산해 버리면 회전할 때마다 7일이 새로 시작돼 상한이 사라집니다.
    """
    access_token = create_access_token(subject_id, subject_type, role)
    refresh_token = generate_refresh_token()

    await refresh_token_repo.create(
        session,
        subject_type=subject_type,
        subject_id=subject_id,
        # 원문이 아니라 해시를 넘깁니다. 원문은 쿠키(관리자)나 응답 바디(앱)로만
        # 나가고 DB 에 남지 않습니다.
        token_hash=hash_refresh_token(refresh_token),
        expires_at=refresh_expires_at,
        user_agent=user_agent,
        ip=ip,
    )

    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        access_expires_at=datetime.now(UTC) + ACCESS_TTL,
        refresh_expires_at=refresh_expires_at,
        subject_type=subject_type,
        subject_id=subject_id,
        role=role,
    )


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
        raise InvalidCredentialsError

    try:
        verify_password(admin.password_hash, password)
    except VerifyMismatchError:
        login_attempts.record_failure(login_id, ip)
        logger.info("로그인 실패: 비밀번호 불일치 (admin=%s, ip=%s)", admin.id, ip)
        raise InvalidCredentialsError from None
    # InvalidHashError 는 잡지 않습니다. 저장된 해시가 깨졌다는 뜻이고,
    # 그건 401 이 아니라 데이터 사고라 500 으로 올라가야 합니다 (core/password.py).

    if admin.status != "active":
        # 비밀번호는 맞았지만 들여보내지 않습니다.
        # 실패로 세지 않는 이유는 추측 시도가 아니기 때문입니다.
        # 응답은 아이디·비밀번호가 틀렸을 때와 똑같습니다 — 여기서 "정지됨"을 알려 주면
        # 비밀번호를 맞혔다는 사실까지 알려 주게 됩니다.
        logger.warning("로그인 거부: 정지된 계정 (admin=%s, ip=%s)", admin.id, ip)
        raise InvalidCredentialsError

    now = datetime.now(UTC)
    login_attempts.record_success(login_id, ip)
    admin.last_login_at = now

    # 로그인은 비밀번호 원문을 아는 유일한 순간이라, 파라미터가 올라갔으면
    # 지금이 다시 해시할 수 있는 유일한 기회입니다 (core/password.py).
    if needs_rehash(admin.password_hash):
        admin.password_hash = hash_password(password)
        logger.info("비밀번호 해시를 새 파라미터로 갱신 (admin=%s)", admin.id)

    pair = await _issue(
        session,
        subject_type=SubjectType.ADMIN,
        subject_id=admin.id,
        role=admin.role,
        refresh_expires_at=now + REFRESH_TTL,
        user_agent=user_agent,
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

    판단 순서에 이유가 있습니다.

        1. 못 찾음    → 거부. 로그아웃했거나 모르는 토큰입니다.
        2. 만료됨     → 거부. **탈취 의심으로 올리지 않습니다** — 만료된 토큰은
                        아무 쓸모가 없어서, 오래 잠들어 있던 탭 때문에 팀 전체를
                        로그아웃시킬 이유가 없습니다.
        3. 폐기됨     → 유예 안이면 탭 경합, 밖이면 탈취.
        4. 계정 확인  → 정지·삭제되었으면 세션을 전부 정리합니다.
    """
    now = datetime.now(UTC)
    row = await refresh_token_repo.get_by_hash(
        session, hash_refresh_token(refresh_token)
    )

    if row is None:
        logger.info("재발급 실패: 모르는 토큰 (ip=%s)", ip)
        raise InvalidRefreshTokenError

    if row.subject_type is not SubjectType.ADMIN:
        # 앱 회원의 refresh 를 관리자 엔드포인트로 보낸 것입니다. 여기서 통과시키면
        # 관리자 role 이 담긴 access token 이 나갑니다.
        # 앱 회원의 재발급은 별도 서비스가 맡습니다 (카카오 카드).
        logger.warning(
            "재발급 거부: 관리자 아님 (subject=%s/%s, ip=%s)",
            row.subject_type.value,
            row.subject_id,
            ip,
        )
        raise InvalidRefreshTokenError

    if row.expires_at <= now:
        logger.info("재발급 실패: 만료된 토큰 (admin=%s)", row.subject_id)
        raise InvalidRefreshTokenError

    if row.revoked_at is not None:
        # 불변식: revoked_at 이 있다는 것은 '회전으로 교체됨'을 뜻합니다.
        # 로그아웃·강제 폐기는 행을 지우므로 여기까지 오지 않습니다.
        if now - row.revoked_at > REFRESH_REUSE_GRACE:
            count = await refresh_token_repo.delete_all_for_subject(
                session, row.subject_type, row.subject_id
            )
            await session.commit()
            logger.warning(
                "재사용 감지: 세션 %d개를 폐기했습니다 (admin=%s, ip=%s)",
                count,
                row.subject_id,
                ip,
            )
            raise TokenReuseDetectedError
        # 유예 안입니다. 같은 쿠키를 쓰는 탭 두 개가 동시에 재발급을 시도한 것으로
        # 보고 통과시킵니다. 진 쪽도 자기 몫의 새 토큰을 받아 갑니다.
        logger.debug("재발급 경합 (admin=%s)", row.subject_id)

    admin = await admin_user_repo.get_by_id(session, row.subject_id)
    if admin is None or admin.status != "active":
        # 계정이 사라졌거나 정지되었습니다. 남은 세션을 여기서 정리합니다 —
        # status 는 '새 로그인'을 막을 뿐이라, 이미 나간 세션은 이렇게 끊어야 합니다.
        await refresh_token_repo.delete_all_for_subject(
            session, SubjectType.ADMIN, row.subject_id
        )
        await session.commit()
        logger.warning("재발급 거부: 쓸 수 없는 계정 (admin=%s)", row.subject_id)
        raise InvalidRefreshTokenError

    if row.revoked_at is None:
        await refresh_token_repo.revoke(session, row, now)

    pair = await _issue(
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
    row = await refresh_token_repo.get_by_hash(
        session, hash_refresh_token(refresh_token)
    )
    if row is None:
        return

    subject_type, subject_id = row.subject_type, row.subject_id
    await refresh_token_repo.delete_one(session, row)
    await session.commit()
    logger.info("로그아웃 (%s=%s)", subject_type.value, subject_id)
