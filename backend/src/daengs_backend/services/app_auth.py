"""앱 회원(카카오 소셜 로그인)의 판단과 트랜잭션 경계.

관리자(services/auth.py)와 **발급·회전 규칙은 공유**하고(services/session.py),
여기 있는 것은 앱 회원만의 판단입니다 — 카카오 신원 확인, 최초 가입, 프로필 갱신,
정지·탈퇴 처리.

HTTP 를 모릅니다. 여기서 나가는 것은 예외와 TokenPair 뿐이고, 그것을 401 로 줄지
403 으로 줄지는 routers/app_auth.py 가 정합니다.

**개인정보 암복호화가 일어나는 곳이 여기입니다.** repositories 는 바이트열만 다루고
(models/app_user.py 주석), core/crypto.py 는 DB 를 모릅니다. 둘을 잇는 자리입니다.
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.crypto import blind_index, encrypt
from daengs_backend.core.kakao import KakaoIdentity, verify_id_token
from daengs_backend.core.subject import SubjectType
from daengs_backend.core.token import REFRESH_TTL
from daengs_backend.models import AppUser
from daengs_backend.repositories import app_user as app_user_repo
from daengs_backend.repositories import pet as pet_repo
from daengs_backend.repositories import refresh_token as refresh_token_repo
from daengs_backend.repositories import walk as walk_repo
from daengs_backend.services import session as session_service
from daengs_backend.services.session import (
    InvalidRefreshTokenError,
    TokenPair,
    TokenReuseDetectedError,
)

logger = logging.getLogger(__name__)

# session.py 것을 그대로 다시 내보냅니다 (services/auth.py 와 같은 이유).
__all__ = [
    "AppAuthError",
    "EmailAlreadyRegisteredError",
    "InvalidRefreshTokenError",
    "SuspendedError",
    "TokenPair",
    "TokenReuseDetectedError",
    "login_with_kakao",
    "logout",
    "refresh",
    "withdraw",
]


class AppAuthError(Exception):
    """앱 회원 인증에 실패했습니다. 아래 것들의 부모입니다."""


class SuspendedError(AppAuthError):
    """이용 정지된 회원입니다.

    **관리자 로그인과 달리 사유를 알려 줍니다.** 그쪽은 정지 사실을 알려 주면
    "비밀번호를 맞혔다"까지 알려 주는 셈이라 숨겼지만, 여기는 이미 카카오가 본인임을
    확인해 준 뒤입니다. 숨길 것이 없고, 숨기면 사용자만 영문을 모릅니다.
    """


class EmailAlreadyRegisteredError(AppAuthError):
    """그 이메일로 **이미 다른 카카오 계정이** 가입되어 있습니다.

    `app_users.email_hash` 의 UNIQUE 가 막습니다 (03_auth.sql — 중복가입 방지).
    카카오 계정을 새로 만들고 같은 이메일을 쓰면 여기로 옵니다. 자동으로 합치지
    않습니다 — 계정 병합은 사람이 확인해야 하는 일입니다.
    """


def _encrypted_email(email: str | None) -> tuple[bytes | None, str | None]:
    """이메일 평문을 (암호문, blind index) 로. 없으면 (None, None) 입니다.

    **빈 문자열을 해시하지 않습니다.** `email_hash` 가 UNIQUE 라, 이메일 동의를 안 받은
    회원끼리 같은 값이 되어 두 번째 가입부터 막힙니다.
    """
    if not email:
        return None, None
    return encrypt(email), blind_index(email)


async def _sync_profile(user: AppUser, identity: KakaoIdentity) -> None:
    """다시 로그인할 때 프로필을 맞춥니다. 바뀐 것이 없으면 아무것도 하지 않습니다.

    **이메일이 안 왔다고 지우지 않습니다.** 동의를 철회한 것과 이번 토큰에 안 실린
    것을 우리가 구분할 방법이 없어서, 지우는 쪽으로 잡으면 멀쩡한 값이 사라집니다.

    비교를 `email_hash` 로 하는 이유는 암호문이 매번 달라서입니다 — `email_enc` 끼리
    비교하면 값이 같아도 항상 다르게 나와 로그인마다 쓸데없이 UPDATE 가 돕니다.
    """
    if identity.email is None:
        return

    new_hash = blind_index(identity.email)
    if user.email_hash == new_hash:
        return

    user.email_enc = encrypt(identity.email)
    user.email_hash = new_hash
    logger.info("이메일 갱신 (app_user=%s)", user.id)


async def login_with_kakao(
    session: AsyncSession,
    *,
    id_token: str,
    user_agent: str | None = None,
    ip: str | None = None,
    expected_nonce: str | None = None,
) -> TokenPair:
    """카카오 id_token 을 검증하고 우리 토큰 한 쌍을 발급합니다.

    **잠금(login_attempts)을 걸지 않습니다.** 비밀번호를 추측하는 통로가 아니라,
    카카오가 이미 인증을 마친 결과를 들고 오는 자리입니다. 여기서 실패하는 것은
    "토큰이 못 믿을 것"뿐이고 반복해도 얻을 게 없습니다.
    """
    identity = await verify_id_token(id_token, expected_nonce=expected_nonce)

    user = await app_user_repo.get_by_kakao_id(session, identity.kakao_id)
    now = datetime.now(UTC)

    if user is None:
        email_enc, email_hash = _encrypted_email(identity.email)
        try:
            user = await app_user_repo.create(
                session,
                kakao_id=identity.kakao_id,
                email_enc=email_enc,
                email_hash=email_hash,
            )
        except IntegrityError:
            # email_hash UNIQUE 입니다. 같은 이메일로 다른 카카오 계정이 이미 있습니다.
            # (kakao_id UNIQUE 는 위에서 조회했으므로 경합이 아니면 여기 걸리지 않습니다)
            await session.rollback()
            logger.warning("가입 거부: 이미 쓰이는 이메일 (kakao_id=%s)", identity.kakao_id)
            raise EmailAlreadyRegisteredError from None
        logger.info("앱 회원 가입 (app_user=%s)", user.id)
    elif user.status == "suspended":
        logger.warning("로그인 거부: 정지된 회원 (app_user=%s, ip=%s)", user.id, ip)
        raise SuspendedError
    elif user.status == "withdrawn":
        # **예전 방식으로 탈퇴한 행**입니다. 지금 withdraw 는 행을 지우므로 새로
        # 생기지 않지만, 2026-09-02 이전에 탈퇴한 회원의 행이 DB 에 남아 있을 수
        # 있습니다. 카카오 회원번호가 UNIQUE 라 새 행을 만들 수 없고, 거부하면 그
        # 사람은 영영 못 들어옵니다. 되살리되 개인정보는 _sync_profile 이 다시 채웁니다.
        user.status = "active"
        logger.info("탈퇴 회원 재가입 (app_user=%s)", user.id)
        await _sync_profile(user, identity)
    else:
        await _sync_profile(user, identity)

    pair = await session_service.issue(
        session,
        subject_type=SubjectType.APP,
        subject_id=user.id,
        # 앱 회원에게는 role 이 없습니다. 넣으면 발급기가 ValueError 로 막습니다.
        role=None,
        refresh_expires_at=now + REFRESH_TTL,
        user_agent=user_agent,
        ip=ip,
    )
    await session.commit()
    logger.info("앱 로그인 성공 (app_user=%s, ip=%s)", user.id, ip)
    return pair


async def refresh(
    session: AsyncSession,
    *,
    refresh_token: str,
    user_agent: str | None = None,
    ip: str | None = None,
) -> TokenPair:
    """refresh 를 새 한 쌍으로 바꿉니다 (회전).

    토큰을 믿어도 되는지는 services/session.py 가 봅니다 — 관리자와 **똑같은** 규칙이고,
    거기에 **주체가 앱 회원인지**까지 확인합니다. 여기서 이어서 하는 것은 회원 확인입니다.
    """
    row = await session_service.take_for_rotation(
        session,
        refresh_token=refresh_token,
        subject_type=SubjectType.APP,
        ip=ip,
    )

    user = await app_user_repo.get_by_id(session, row.subject_id)
    if user is None or user.status != "active":
        # 정지·탈퇴했거나 사라졌습니다. 남은 세션을 여기서 정리합니다 —
        # status 는 '새 로그인'을 막을 뿐이라, 이미 나간 세션은 이렇게 끊어야 합니다.
        await session_service.drop_all(session, SubjectType.APP, row.subject_id)
        await session.commit()
        logger.warning("재발급 거부: 쓸 수 없는 회원 (app_user=%s)", row.subject_id)
        raise InvalidRefreshTokenError

    pair = await session_service.issue(
        session,
        subject_type=SubjectType.APP,
        subject_id=user.id,
        role=None,
        # **연장하지 않습니다.** 옛 행의 만료를 그대로 물려받습니다.
        refresh_expires_at=row.expires_at,
        user_agent=user_agent,
        ip=ip,
    )
    await session.commit()
    return pair


async def logout(session: AsyncSession, *, refresh_token: str) -> None:
    """세션 하나를 끊습니다. 없는 토큰이어도 조용히 성공합니다.

    **행을 지웁니다 (revoke 가 아닙니다).** revoked_at 을 찍으면 재사용 감지의
    유예 창을 타고 10초 동안 통과해 버립니다 (repositories/refresh_token.py).
    """
    row = await session_service.find(session, refresh_token)
    if row is None:
        return

    subject_id = row.subject_id
    await refresh_token_repo.delete_one(session, row)
    await session.commit()
    logger.info("앱 로그아웃 (app_user=%s)", subject_id)


async def withdraw(session: AsyncSession, *, app_user_id: uuid.UUID) -> None:
    """탈퇴. 강아지·산책(좌표 포함)·세션을 지우고 **회원 행까지 지웁니다.**

    공개 삭제 안내(daengs-legal `delete.html`)가 "계정 정보 · 반려동물 · 산책 기록과
    좌표 전부 · 세션이 바로 삭제된다"고 약속합니다. 구글은 그 문서와 데이터 안전
    양식을 앱 동작과 대조하고, 개인정보보호법도 탈퇴 뒤의 목적 없는 보관을 막습니다.
    그래서 소프트 삭제(status='withdrawn' + 암호문 비우기)에서 진짜 삭제로 바꿨습니다 —
    예전 방식은 pets·walks 의 `ON DELETE CASCADE` 가 한 번도 발동하지 않아 좌표가
    전부 남았고, 재로그인하면 그대로 되살아났습니다 (2026-09-02 출시 점검).

    **순서가 있습니다.** 산책 → 강아지 → 세션 → 회원. 회원 행만 지워도 DB 캐스케이드가
    나머지를 따라 지우지만, 몇 건을 지웠는지 로그에 남기려면 먼저 세어야 합니다.
    `walk_points`·`walk_pets` 는 walks 의 캐스케이드가, `primary_pet_id` 는 회원 행이
    같이 사라지므로 따로 손대지 않습니다.

    같은 카카오 계정으로 다시 로그인하면 **새 회원**이 됩니다 (created_at 도 새로).
    "이미 탈퇴했던 사람"을 서버가 기억하지 않는 것이 의도입니다 — 기억하려면
    kakao_id 를 남겨야 하는데 그것도 개인정보입니다.

    **카카오 연결 끊기(unlink)는 여기서 하지 않습니다.** 앱이 SDK 로 합니다 —
    서버가 하려면 어드민 키를 둬야 하는데, 그 키 하나로 전 회원을 조작할 수 있습니다.
    그래서 앱이 unlink 에 실패해도 우리 쪽 탈퇴는 그대로 진행됩니다.

    **access 토큰은 만료(5분)까지 살아 있습니다.** `CurrentAppUser` 가 DB 를 보지 않아서
    그렇습니다 — 회원 행이 없으니 그 토큰으로 할 수 있는 것은 빈 목록 조회뿐입니다.
    """
    user = await app_user_repo.get_by_id(session, app_user_id)
    if user is None:
        # 이미 없습니다. 탈퇴는 여러 번 불러도 같은 결과여야 합니다.
        return

    walks = await walk_repo.delete_for_owner(session, user.id)
    pets = await pet_repo.delete_for_owner(session, user.id)
    sessions = await session_service.drop_all(session, SubjectType.APP, user.id)
    await app_user_repo.delete(session, user)
    await session.commit()
    logger.info(
        "앱 회원 탈퇴 (app_user=%s, 산책 %d건 · 강아지 %d마리 · 세션 %d개 삭제)",
        app_user_id,
        walks,
        pets,
        sessions,
    )
