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
from daengs_backend.repositories import chat as chat_repo
from daengs_backend.repositories import refresh_token as refresh_token_repo
from daengs_backend.repositories import walk as walk_repo
from daengs_backend.services import dogcard as card_service
from daengs_backend.services import pet as pet_service
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
        # **탈퇴했다가 다시 로그인한 경우 되살립니다.** 카카오 회원번호가 같으므로
        # 새 행을 만들 수 없고(UNIQUE), 거부하면 그 사람은 영영 못 들어옵니다.
        # 탈퇴 때 개인정보를 지웠으므로 아래 _sync_profile 이 다시 채웁니다.
        # created_at 은 최초 가입 시각으로 남습니다 — 재가입 시각이 필요해지면
        # 그때 컬럼을 더하세요.
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
    """탈퇴. 개인정보를 파기하고 세션을 전부 끊습니다. 행은 남깁니다.

    **행을 지우지 않는 이유**는 `kakao_id` 로 "이미 탈퇴한 사람"을 알아보기 위해서입니다.
    지우면 재가입할 때 완전히 새 사람이 되고, 운영 데이터의 참조도 끊깁니다.

    **카카오 연결 끊기(unlink)는 여기서 하지 않습니다.** 앱이 SDK 로 합니다 —
    서버가 하려면 어드민 키를 둬야 하는데, 그 키 하나로 전 회원을 조작할 수 있습니다.
    그래서 앱이 unlink 에 실패해도 우리 쪽 탈퇴는 그대로 진행됩니다. 사용자가 카카오
    설정에서 연결된 앱 목록을 보면 남아 있을 수 있는데, 다시 로그인하면 위
    login_with_kakao 가 되살립니다.
    """
    user = await app_user_repo.get_by_id(session, app_user_id)
    if user is None:
        # 이미 없습니다. 탈퇴는 여러 번 불러도 같은 결과여야 합니다.
        return

    try:
        # **산책이 먼저입니다.** 강아지를 먼저 지우면 walk_pets 연결만 사라지고,
        # 사람 소유인 walks와 집·생활권을 드러내는 좌표는 그대로 남습니다.
        deleted_walks = await walk_repo.delete_all_for_owner(session, user.id)
        deleted_pets = await pet_service.delete_all_for_owner(session, user.id)

        # 대화는 pets 를 지울 때 pet_id 로 함께 CASCADE 되지만, **그것에 기대지
        # 않습니다.** app_users 행은 탈퇴해도 남기므로 app_user_id 쪽 CASCADE 는
        # 영영 돌지 않고, 대화가 강아지와의 연결을 잃는 날이 오면 사람의 질문 원문만
        # 조용히 남습니다. 같은 트랜잭션에서 명시로 지웁니다 (turn 은 CASCADE).
        await chat_repo.delete_all_for_user(session, user.id)

        # 도감 카드도 **같은 이유로 명시 삭제**입니다 — app_users 행을 남기므로 FK
        # CASCADE 가 영영 안 돕니다. 그리고 행만 지워서는 부족합니다: 얼굴 그림이
        # 저장소에 있고 **저장소에는 FK 가 없어서** 아무도 안 치웁니다.
        deleted_cards = await card_service.cleanup_for_owner(session, user.id)

        user.status = "withdrawn"
        # 개인정보 파기. **암호문을 지우는 것으로 파기가 됩니다** — 평문은 어디에도 없습니다.
        user.email_enc = None
        user.email_hash = None
        user.phone_enc = None
        user.name_enc = None
        user.room_name = None
        user.primary_pet_id = None

        token_count = await session_service.drop_all(session, SubjectType.APP, user.id)
        await session.commit()
    except Exception:
        # 요청 의존성도 닫힐 때 rollback하지만, 여기서 명시해야 이 함수를 다른
        # 진입점에서 불러도 반쪽 탈퇴가 남지 않습니다.
        await session.rollback()
        raise
    logger.info(
        "앱 회원 탈퇴 (app_user=%s, 강아지 %d마리, 산책 %d건, 도감 카드 %d장, "
        "끊은 세션 %d개)",
        user.id,
        deleted_pets,
        deleted_walks,
        deleted_cards,
        token_count,
    )
