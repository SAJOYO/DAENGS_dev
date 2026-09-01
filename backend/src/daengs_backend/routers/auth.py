"""인증 HTTP 경계 — 쿠키를 굽고, 서비스의 예외를 상태 코드로 바꿉니다.

판단은 여기 없습니다. "이 비밀번호가 맞나", "이 토큰을 믿어도 되나"는 전부
services/auth.py 가 정하고, 여기서는 그 결과를 401 / 429 로 옮기기만 합니다.

**토큰은 쿠키로만 나갑니다.** 응답 본문에 같이 실으면 httpOnly 로 둔 의미가 없습니다.
"""

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.config import settings
from daengs_backend.core.database import get_session
from daengs_backend.core.deps import ACCESS_COOKIE, CurrentAdmin, Principal
from daengs_backend.repositories import admin_user as admin_user_repo
from daengs_backend.schemas.auth import LoginRequest, MeResponse, SessionResponse
from daengs_backend.services import auth as auth_service
from daengs_backend.services import login_attempts

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "daengs_refresh"

# 쿠키 경로를 "/" 로 둡니다.
#
# refresh 쿠키만 /auth 로 좁히는 편이 깔끔하지만, 프론트는 nginx 를 거쳐
# `/api/auth/refresh` 로 오고 개발 PC 에서 직접 띄우면 `/auth/refresh` 로 옵니다.
# 경로가 갈리면 한쪽에서 쿠키가 안 실려 조용히 로그아웃됩니다.
# 같은 오리진 + httpOnly 라 넓혀 두는 대가가 크지 않습니다.
_COOKIE_PATH = "/"


def _client_ip(request: Request) -> str:
    """실패 횟수를 셀 기준이 되는 IP.

    nginx 의 `proxy_set_header X-Real-IP $remote_addr` 는 클라이언트가 보낸 같은
    이름의 헤더를 **덮어씁니다.** 그래서 배포 환경에서는 위조할 수 없습니다.
    backend 가 포트를 열지 않아 nginx 말고는 들어올 길이 없는 것도 전제입니다 (D-005).

    `uv run dev` 로 직접 띄우면 앞에 nginx 가 없어서 이 헤더를 믿을 수 없습니다.
    개발 PC 한정이라 그대로 둡니다.
    """
    forwarded = request.headers.get("x-real-ip")
    if forwarded:
        return forwarded
    return request.client.host if request.client else "unknown"


def _set_session_cookies(response: Response, pair: auth_service.TokenPair) -> None:
    """access / refresh 를 httpOnly 쿠키로 굽습니다.

    httponly : JS 가 못 읽습니다. XSS 가 들어와도 토큰을 퍼가지 못합니다
               (localStorage 에 두면 그대로 털립니다).
    samesite : lax. 다른 사이트에서 건너온 POST 에는 쿠키가 안 실려 CSRF 를 막습니다.
               none 으로 하려면 secure 가 필수인데 지금은 http 입니다.
    secure   : settings.cookie_secure. **HTTPS 로 옮기면 켜야 합니다.**
    """
    now = datetime.now(UTC)
    response.set_cookie(
        ACCESS_COOKIE,
        pair.access_token,
        max_age=int((pair.access_expires_at - now).total_seconds()),
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path=_COOKIE_PATH,
    )
    response.set_cookie(
        REFRESH_COOKIE,
        pair.refresh_token,
        max_age=int((pair.refresh_expires_at - now).total_seconds()),
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path=_COOKIE_PATH,
    )


def _session_response(pair: auth_service.TokenPair) -> SessionResponse:
    """TokenPair 를 관리자 세션 응답으로. **토큰 문자열은 담기지 않습니다.**

    `pair.role` 은 주체 종류에 따라 None 일 수 있는데(앱 회원), 이 라우터로는
    관리자 pair 만 옵니다. 그래도 확인하는 이유는 빈 문자열로 뭉개면 프론트가
    아무 권한도 없는 화면을 그리면서 원인은 아무 데도 안 남기 때문입니다.
    여기 걸리면 서비스 계층이 잘못 부른 것이라 500 이 맞습니다.
    """
    if pair.role is None:
        raise RuntimeError(
            f"관리자 라우터에 {pair.subject_type.value} 세션이 왔습니다."
        )
    return SessionResponse(
        admin_id=pair.subject_id,
        role=pair.role,
        access_expires_at=pair.access_expires_at,
        refresh_expires_at=pair.refresh_expires_at,
    )


def _clear_session_cookies(response: Response) -> None:
    """쿠키를 지웁니다. 굽을 때와 **같은 path** 여야 지워집니다."""
    for name in (ACCESS_COOKIE, REFRESH_COOKIE):
        response.delete_cookie(
            name,
            httponly=True,
            samesite="lax",
            secure=settings.cookie_secure,
            path=_COOKIE_PATH,
        )


@router.post("/login")
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SessionResponse:
    """아이디·비밀번호로 로그인하고 쿠키를 받습니다.

    실패는 전부 401 에 같은 메시지입니다 — 아이디가 없는 것과 비밀번호가 틀린 것을
    나누면 어느 계정이 존재하는지가 새어 나갑니다.
    잠긴 상태만 429 인데, 그건 계정 존재와 무관하게 걸리므로 알려 줘도 됩니다.
    """
    try:
        pair = await auth_service.login(
            session,
            login_id=payload.login_id,
            password=payload.password,
            user_agent=request.headers.get("user-agent"),
            ip=_client_ip(request),
        )
    except login_attempts.LockedOutError as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "로그인 시도가 너무 많습니다. 잠시 후 다시 시도해 주세요.",
            # 언제 풀리는지 알려 줍니다. 계정 존재 여부와 무관한 정보입니다.
            headers={"Retry-After": str(int(exc.retry_after.total_seconds()))},
        ) from None
    except auth_service.InvalidCredentialsError:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "아이디 또는 비밀번호가 올바르지 않습니다."
        ) from None

    _set_session_cookies(response, pair)
    return _session_response(pair)


@router.post("/refresh")
async def refresh(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SessionResponse:
    """refresh 쿠키로 새 한 쌍을 받습니다. 쓴 토큰은 즉시 폐기됩니다 (회전).

    실패하면 **쿠키를 지웁니다.** 죽은 토큰을 브라우저에 남겨 두면 다음 요청마다
    같은 실패를 반복하고, 재사용 감지에 다시 걸릴 수도 있습니다.
    """
    token = request.cookies.get(REFRESH_COOKIE)
    if token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "인증이 필요합니다.")

    try:
        pair = await auth_service.refresh(
            session,
            refresh_token=token,
            user_agent=request.headers.get("user-agent"),
            ip=_client_ip(request),
        )
    except auth_service.TokenReuseDetectedError:
        # 서비스가 이미 그 계정의 세션을 전부 끊었습니다. 여기서는 이 브라우저의
        # 쿠키만 정리합니다. 응답은 아래 일반 실패와 같습니다 —
        # "탈취가 감지됐다"를 알려 주면 공격자에게도 알려 주는 셈입니다.
        _clear_session_cookies(response)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "다시 로그인해 주세요."
        ) from None
    except auth_service.InvalidRefreshTokenError:
        _clear_session_cookies(response)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "다시 로그인해 주세요."
        ) from None

    _set_session_cookies(response, pair)
    return _session_response(pair)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """이 세션을 끊습니다. 쿠키가 없거나 이미 끊긴 세션이어도 204 입니다.

    **인증을 요구하지 않습니다.** access 가 만료된 상태에서도 로그아웃은 되어야 합니다.
    남의 refresh 토큰을 알아야만 남을 로그아웃시킬 수 있는데, 그걸 안다면
    이미 로그인할 수 있는 상태라 새로 열리는 문이 없습니다.
    """
    token = request.cookies.get(REFRESH_COOKIE)
    if token is not None:
        await auth_service.logout(session, refresh_token=token)

    _clear_session_cookies(response)


@router.get("/me")
async def me(
    admin: CurrentAdmin,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MeResponse:
    """지금 로그인한 관리자와 권한 목록.

    **DB 를 다시 읽습니다.** 토큰 안의 role 은 최대 5분 낡을 수 있어서, 화면 구성을
    그 값으로 하면 권한을 내린 사람에게 잠시 버튼이 보입니다.

    JWE 라 프론트가 토큰을 열어 볼 수 없으니, 이 엔드포인트가 자기 권한을 아는
    유일한 통로입니다.
    """
    row = await admin_user_repo.get_by_id(session, admin.admin_id)
    if row is None or row.status != "active":
        # 토큰은 살아 있는데 계정이 사라졌거나 정지되었습니다.
        # 5분 창의 반대쪽 끝이라, 여기서라도 막아야 합니다.
        logger.warning("me: 쓸 수 없는 계정 (admin=%s)", admin.admin_id)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "다시 로그인해 주세요.")

    fresh = Principal(admin_id=row.id, role=row.role)
    return MeResponse(
        admin_id=row.id,
        login_id=row.login_id,
        name=row.name,
        role=row.role,
        # 정렬해서 주는 이유는 응답이 매번 같아 보이게 하기 위해서입니다
        # (frozenset 은 순서가 없습니다).
        permissions=sorted(p.value for p in fresh.permissions),
        last_login_at=row.last_login_at,
    )
