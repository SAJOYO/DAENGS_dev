"""세션 발급과 회전 — **관리자와 앱 회원이 공유합니다.**

D-015 가 정한 규칙(절대 만료 · 회전 · 재사용 감지 · 10초 유예)은 주체 종류와 무관합니다.
D-016 이 `refresh_tokens` 를 한 테이블로 둔 이유도 이것입니다 — 규칙이 두 벌이 되면
그중 한쪽만 고치는 날이 옵니다. 그 규칙의 **유일한 구현**이 이 파일입니다.

여기에 없는 것은 **"이 사람이 로그인해도 되나"** 입니다. 관리자는 비밀번호를 보고
앱 회원은 카카오 id_token 을 보는데, 그건 각자의 서비스(auth.py / app_auth.py)가
합니다. 여기는 "이 refresh 를 아직 믿어도 되나"까지만 봅니다.

    services/auth.py       관리자 — 비밀번호 확인 → session.issue
    services/app_auth.py   앱 회원 — 카카오 검증 → session.issue
    services/session.py    양쪽 공통 — 발급 · 회전 · 재사용 감지
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.subject import SubjectType
from daengs_backend.core.token import (
    ACCESS_TTL,
    REFRESH_REUSE_GRACE,
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
)
from daengs_backend.models import RefreshToken
from daengs_backend.repositories import refresh_token as refresh_token_repo

logger = logging.getLogger(__name__)

__all__ = [
    "InvalidRefreshTokenError",
    "SessionError",
    "TokenPair",
    "TokenReuseDetectedError",
    "drop_all",
    "find",
    "issue",
    "take_for_rotation",
]


class SessionError(Exception):
    """세션을 이어갈 수 없습니다. 아래 둘의 부모입니다."""


class InvalidRefreshTokenError(SessionError):
    """모르는 토큰이거나, 로그아웃했거나, 만료되었거나, 다른 주체의 것입니다.

    조용히 401 입니다. **왜 실패했는지 응답으로 나누지 마세요** — 어느 토큰이
    존재했는지가 새어 나갑니다.
    """


class TokenReuseDetectedError(SessionError):
    """**이미 회전으로 교체된 토큰이 유예 시간을 한참 지나 다시 들어왔습니다.**

    정상 사용자는 새 토큰을 받아 갔으므로 옛 것을 다시 쓸 이유가 없습니다.
    탈취를 의심하고 그 주체의 세션을 **전부** 끊은 뒤 이 예외를 냅니다.
    """


@dataclass(frozen=True)
class TokenPair:
    """새로 발급한 토큰 한 쌍과, 쿠키를 굽는 데 필요한 시각들.

    **전달 방식은 여기서 정하지 않습니다.** 관리자 웹은 이 값을 httpOnly 쿠키로 굽고,
    네이티브 앱은 응답 바디로 받아 Bearer 로 보냅니다 (routers/ 의 몫).
    """

    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime
    subject_type: SubjectType
    subject_id: uuid.UUID
    #: 관리자만 가집니다. 앱 회원은 None 입니다 (`app_users` 에 role 이 없습니다).
    role: str | None


async def issue(
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

    refresh 의 만료를 인자로 받는 이유는 **회전할 때 연장하지 않기 위해서**입니다.
    로그인은 now + REFRESH_TTL 을 넘기고, 회전은 옛 행의 expires_at 을 그대로 넘깁니다.
    여기서 계산해 버리면 회전할 때마다 수명이 새로 시작돼 상한이 사라집니다.
    """
    access_token = create_access_token(subject_id, subject_type, role)
    refresh_token = generate_refresh_token()

    await refresh_token_repo.create(
        session,
        subject_type=subject_type,
        subject_id=subject_id,
        # 원문이 아니라 해시를 넘깁니다. 원문은 쿠키나 응답 바디로만 나가고
        # DB 에 남지 않습니다.
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


async def find(session: AsyncSession, refresh_token: str) -> RefreshToken | None:
    """원문으로 세션 행을 찾습니다. 판단은 하지 않습니다 (로그아웃이 씁니다)."""
    return await refresh_token_repo.get_by_hash(
        session, hash_refresh_token(refresh_token)
    )


async def take_for_rotation(
    session: AsyncSession,
    *,
    refresh_token: str,
    subject_type: SubjectType,
    ip: str | None = None,
) -> RefreshToken:
    """회전에 써도 되는 행을 돌려줍니다. 못 믿을 것은 전부 예외입니다.

    판단 순서에 이유가 있습니다.

        1. 못 찾음      → 거부. 로그아웃했거나 모르는 토큰입니다.
        2. 주체가 다름  → 거부. **앱 회원 refresh 로 관리자 토큰을 받아가면 안 됩니다.**
        3. 만료됨       → 거부. **탈취 의심으로 올리지 않습니다** — 만료된 토큰은
                          아무 쓸모가 없어서, 오래 잠들어 있던 탭 하나 때문에
                          그 계정 전체를 로그아웃시킬 이유가 없습니다.
        4. 폐기됨       → 유예 안이면 탭 경합, 밖이면 탈취.

    **계정이 살아 있는지는 여기서 보지 않습니다.** 관리자는 `status`, 앱 회원은
    탈퇴 여부까지 봐야 해서 확인할 내용이 다릅니다. 부르는 쪽이 이어서 하세요.
    """
    now = datetime.now(UTC)
    row = await find(session, refresh_token)

    if row is None:
        logger.info("재발급 실패: 모르는 토큰 (ip=%s)", ip)
        raise InvalidRefreshTokenError

    if row.subject_type is not subject_type:
        # 앱 회원 refresh 를 관리자 엔드포인트로 보낸 경우(또는 그 반대)입니다.
        # 통과시키면 role 이 담긴 access token 이 엉뚱한 사람에게 나갑니다.
        logger.warning(
            "재발급 거부: 주체가 다릅니다 (토큰=%s, 요청=%s, ip=%s)",
            row.subject_type.value,
            subject_type.value,
            ip,
        )
        raise InvalidRefreshTokenError

    if row.expires_at <= now:
        logger.info(
            "재발급 실패: 만료된 토큰 (%s=%s)", subject_type.value, row.subject_id
        )
        raise InvalidRefreshTokenError

    if row.revoked_at is not None:
        # 불변식: revoked_at 이 있다는 것은 '회전으로 교체됨'을 뜻합니다.
        # 로그아웃·강제 폐기는 행을 지우므로 여기까지 오지 않습니다
        # (repositories/refresh_token.py).
        if now - row.revoked_at > REFRESH_REUSE_GRACE:
            count = await drop_all(session, row.subject_type, row.subject_id)
            # 예외로 빠져나가더라도 **폐기는 남아야** 합니다. 여기서 commit 하지 않으면
            # 라우터가 401 을 만드는 동안 세션이 롤백되어 아무것도 안 끊깁니다.
            await session.commit()
            logger.warning(
                "재사용 감지: 세션 %d개를 폐기했습니다 (%s=%s, ip=%s)",
                count,
                row.subject_type.value,
                row.subject_id,
                ip,
            )
            raise TokenReuseDetectedError
        # 유예 안입니다. 같은 토큰을 쓰는 요청 두 개가 거의 동시에 재발급을 시도한
        # 것으로 보고 통과시킵니다. 진 쪽도 자기 몫의 새 토큰을 받아 갑니다.
        logger.debug("재발급 경합 (%s=%s)", row.subject_type.value, row.subject_id)
    else:
        await refresh_token_repo.revoke(session, row, now)

    return row


async def drop_all(
    session: AsyncSession, subject_type: SubjectType, subject_id: uuid.UUID
) -> int:
    """그 주체의 세션을 전부 끊습니다. 지운 행 수를 돌려줍니다.

    쓰는 곳은 셋입니다 — 탈취가 의심될 때, 계정이 정지·탈퇴했을 때, 관리자가 강제로
    끊을 때. `revoked_at` 이 아니라 **행을 지웁니다**. 시각만 찍으면 다른 세션들이
    10초 유예 창을 타고 새 토큰을 받아 가서, 끊으려던 것이 안 끊깁니다.
    """
    return await refresh_token_repo.delete_all_for_subject(
        session, subject_type, subject_id
    )
