"""앱 회원 인증 HTTP 경계 — 서비스의 예외를 상태 코드로 바꿉니다.

판단은 여기 없습니다. "이 id_token 을 믿어도 되나"는 core/kakao.py 가,
"이 사람이 로그인해도 되나"는 services/app_auth.py 가 정합니다.

**쿠키를 굽지 않습니다.** 토큰은 응답 본문으로 나갑니다 — 네이티브 앱에는 쿠키
저장소가 없습니다 (schemas/app_auth.py 참고). 관리자 라우터와 정반대인 유일한 지점입니다.

경로를 `/auth/app/*` 으로 묶은 이유는 관리자의 `/auth/refresh` 와 이름이 겹치기
때문입니다. 같은 이름의 엔드포인트가 주체에 따라 다르게 동작하면, nginx 설정이나
클라이언트 코드에서 한 글자 차이로 엉뚱한 쪽을 부르게 됩니다.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.crypto import decrypt
from daengs_backend.core.database import get_session
from daengs_backend.core.deps import CurrentAppUser
from daengs_backend.core.kakao import KakaoIdTokenInvalidError, KakaoUnavailableError
from daengs_backend.models import AppUser
from daengs_backend.repositories import app_user as app_user_repo
from daengs_backend.schemas.app_auth import (
    AppMeResponse,
    AppProfileUpdate,
    AppSessionResponse,
    KakaoLoginRequest,
    RefreshRequest,
)
from daengs_backend.services import app_auth as app_auth_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/app", tags=["app-auth"])


def _client_ip(request: Request) -> str:
    """표시·로그용 IP. 인증 판단에는 쓰지 않습니다.

    nginx 가 `X-Real-IP` 를 덮어쓰므로 배포 환경에서는 위조할 수 없습니다 (D-005).
    """
    forwarded = request.headers.get("x-real-ip")
    if forwarded:
        return forwarded
    return request.client.host if request.client else "unknown"


def _session_response(pair: app_auth_service.TokenPair) -> AppSessionResponse:
    """TokenPair 를 앱 세션 응답으로.

    `pair.role` 은 앱 회원이면 반드시 None 입니다. 여기서 확인하지 않는 이유는
    발급기(`core/token.py`)가 이미 막고 있기 때문입니다 — 앱 주체에 role 을 넣으면
    ValueError 로 터져서 여기까지 오지 못합니다.
    """
    return AppSessionResponse(
        app_user_id=pair.subject_id,
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        access_expires_at=pair.access_expires_at,
        refresh_expires_at=pair.refresh_expires_at,
    )


@router.post("/kakao")
async def login_with_kakao(
    payload: KakaoLoginRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AppSessionResponse:
    """카카오 `id_token` 으로 로그인합니다. 최초면 회원이 만들어집니다.

    상태 코드를 나눠 두는 이유는 **앱이 해야 할 일이 다르기** 때문입니다.

        401  토큰을 못 믿음   → 카카오 로그인을 다시 하세요
        403  정지된 회원      → 다시 해도 소용없습니다. 문의 안내를 띄우세요
        409  이메일 중복      → 기존 계정으로 로그인하도록 안내하세요
        503  카카오가 응답 없음 → 잠시 후 재시도. **사용자 잘못이 아닙니다**

    특히 503 을 401 로 뭉개면 앱이 '로그인 실패'로 알아듣고 다시 로그인하라고 하는데,
    다시 해도 똑같이 실패합니다.
    """
    try:
        pair = await app_auth_service.login_with_kakao(
            session,
            id_token=payload.id_token,
            expected_nonce=payload.nonce,
            user_agent=request.headers.get("user-agent"),
            ip=_client_ip(request),
        )
    except KakaoUnavailableError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "카카오 인증 서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.",
        ) from None
    except KakaoIdTokenInvalidError:
        # 무엇이 틀렸는지(서명·만료·aud·nonce)는 응답에 담지 않습니다.
        # 검증을 통과하는 조건을 알려 주는 셈입니다. 서버 로그에는 남습니다.
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "카카오 로그인 정보를 확인할 수 없습니다."
        ) from None
    except app_auth_service.SuspendedError:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "이용이 정지된 계정입니다."
        ) from None
    except app_auth_service.EmailAlreadyRegisteredError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "해당 이메일로 이미 가입된 계정이 있습니다.",
        ) from None

    return _session_response(pair)


@router.post("/refresh")
async def refresh(
    payload: RefreshRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AppSessionResponse:
    """refresh 로 새 한 쌍을 받습니다. **쓴 토큰은 즉시 폐기됩니다** (회전).

    응답의 `refresh_token` 을 반드시 저장해 덮어쓰세요. 옛 것을 다시 쓰면 재사용
    감지에 걸려 그 회원의 세션이 전부 끊깁니다 (D-015).

    실패는 전부 401 에 같은 메시지입니다 — 탈취가 감지됐다고 알려 주면 공격자에게도
    알려 주는 셈입니다.
    """
    try:
        pair = await app_auth_service.refresh(
            session,
            refresh_token=payload.refresh_token,
            user_agent=request.headers.get("user-agent"),
            ip=_client_ip(request),
        )
    except (
        app_auth_service.TokenReuseDetectedError,
        app_auth_service.InvalidRefreshTokenError,
    ):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "다시 로그인해 주세요."
        ) from None

    return _session_response(pair)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    payload: RefreshRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """이 세션을 끊습니다. 모르는 토큰이어도 204 입니다.

    **인증을 요구하지 않습니다.** access 가 만료된 상태에서도 로그아웃은 되어야 합니다.
    남의 refresh 를 알아야만 남을 로그아웃시킬 수 있는데, 그걸 안다면 이미 로그인할 수
    있는 상태라 새로 열리는 문이 없습니다.
    """
    await app_auth_service.logout(session, refresh_token=payload.refresh_token)


@router.get("/me")
async def me(
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AppMeResponse:
    """지금 로그인한 회원. **DB 를 다시 읽습니다.**

    토큰에는 회원 id 밖에 없고, 정지·탈퇴는 최대 5분 늦게 반영됩니다.
    여기서 한 번 더 확인해 그 창의 반대쪽 끝을 막습니다.
    """
    row = await app_user_repo.get_by_id(session, user.app_user_id)
    if row is None or row.status != "active":
        logger.warning("app me: 쓸 수 없는 회원 (app_user=%s)", user.app_user_id)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "다시 로그인해 주세요.")

    return _to_me(row)


@router.patch("/me")
async def update_me(
    body: AppProfileUpdate,
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AppMeResponse:
    """회원이 스스로 고치는 것. 지금은 미니룸 이름표뿐입니다.

    **`room_name` 을 null(또는 공백)로 보내면 되돌립니다** — 다시 대표 강아지를
    따라갑니다. 빈 문자열로 저장하지 않는 이유는, 그러면 "아직 안 정했다" 와
    "정해서 지웠다" 가 같은 값이 되어 앱이 무엇을 그릴지 못 정하기 때문입니다.

    이름을 서버가 지어 주지 않습니다. 받침에 따라 "이네"/"네" 가 갈리는 것은 한국어
    규칙이라 앱의 것이고, 서버가 같이 지으면 규칙이 두 벌이 됩니다.
    """
    row = await app_user_repo.get_by_id(session, user.app_user_id)
    if row is None or row.status != "active":
        logger.warning("app patch me: 쓸 수 없는 회원 (app_user=%s)", user.app_user_id)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "다시 로그인해 주세요.")

    row.room_name = body.room_name
    await session.commit()
    return _to_me(row)


def _to_me(row: AppUser) -> AppMeResponse:
    return AppMeResponse(
        app_user_id=row.id,
        kakao_id=row.kakao_id,
        # 복호화는 여기서 합니다. 본인 요청이고 본인 것만 나갑니다.
        email=decrypt(row.email_enc) if row.email_enc else None,
        status=row.status,
        created_at=row.created_at,
        room_name=row.room_name,
    )


@router.post("/withdraw", status_code=status.HTTP_204_NO_CONTENT)
async def withdraw(
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """탈퇴합니다. 개인정보를 파기하고 세션을 전부 끊습니다.

    **카카오 연결 끊기(unlink)는 앱이 SDK 로 해야 합니다.** 서버가 하려면 어드민 키를
    둬야 하는데, 그 키 하나로 전 회원을 조작할 수 있습니다. 앱은 이 API 를 부르기 전이나
    후에 unlink 를 부르세요 — 실패해도 우리 쪽 탈퇴는 그대로 진행됩니다.

    여러 번 불러도 같은 결과입니다. 다만 첫 호출로 세션이 끊기므로 두 번째 호출은
    401 을 받습니다.
    """
    await app_auth_service.withdraw(session, app_user_id=user.app_user_id)
