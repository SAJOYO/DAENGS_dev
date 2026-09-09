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

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.exc import IntegrityError
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
    NicknameAvailability,
    RefreshRequest,
)
from daengs_backend.services import app_auth as app_auth_service
from daengs_backend.services import pet as pet_service

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
    """회원이 스스로 고치는 것 — 미니룸 이름표와 닉네임.

    ⚠️ **보낸 칸만 바뀝니다.** `{"nickname": "..."}` 만 보내면 이름표는 그대로입니다.

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

    sent = body.model_fields_set

    # **보낸 칸만 바꿉니다.** 예전에는 `row.room_name = body.room_name` 한 줄이었는데,
    # 칸이 둘이 되는 순간 그것이 **닉네임만 고치려는 요청에 이름표를 같이 지웁니다** —
    # 안 보낸 칸도 모델에서는 None 이라 "되돌려 달라"와 구분이 안 됩니다.
    if "room_name" in sent:
        row.room_name = body.room_name

    if "nickname" in sent:
        if body.nickname is None:
            # 이름표와 달리 **비울 수 없습니다.** 조용히 무시하면 앱은 바뀐 줄 알고
            # 화면에서 옛 이름을 지웁니다 (schemas/app_auth.py 의 그 칸 주석).
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "닉네임은 비울 수 없습니다."
            )
        if body.nickname.lower() == (row.nickname or "").lower():
            # **자기 이름입니다.** 중복 검사를 하면 자기 자신에 걸립니다. 그대로 넣는
            # 이유는 `neo` → `Neo` 처럼 **대소문자만 바꾸는 것도 진짜 편집**이라서입니다 —
            # 건너뛰면 사용자가 고쳤는데 화면이 안 바뀝니다.
            row.nickname = body.nickname
        elif await app_user_repo.is_nickname_taken(session, body.nickname):
            raise HTTPException(
                status.HTTP_409_CONFLICT, "다른 분이 쓰고 있는 이름이에요."
            )
        else:
            row.nickname = body.nickname

    try:
        await session.commit()
    except IntegrityError:
        # 위에서 물어본 뒤 커밋 전에 남이 채갔습니다. **여기가 진짜 방어입니다** —
        # `lower(nickname)` UNIQUE 인덱스가 막아 준 것이고, 앞의 조회는 참고였습니다.
        await session.rollback()
        logger.info("닉네임 경합 (app_user=%s)", user.app_user_id)
        raise HTTPException(
            status.HTTP_409_CONFLICT, "다른 분이 쓰고 있는 이름이에요."
        ) from None
    return _to_me(row)


@router.get("/nickname/available")
async def nickname_available(
    user: CurrentAppUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    value: Annotated[str, Query(min_length=1, max_length=30)],
) -> NicknameAvailability:
    """앱이 **입력하는 동안** 부릅니다. 쓸 수 있는 이름인지 미리 알려 줍니다.

    ⚠️ **판정은 참고용입니다.** 여기서 `true` 를 받아도 저장할 때 409 가 날 수 있습니다 —
    물어본 뒤 저장하기 전에 남이 채갈 수 있어서입니다. 진짜 방어는
    `lower(nickname)` UNIQUE 인덱스이고, 이 API 는 **사용자가 다 치고 나서 거절당하는
    일을 줄이는 것**이 목적입니다.

    **로그인해야 부를 수 있습니다.** 로그인 뒤에만 쓰는 화면이기도 하고, 열어 두면
    후보 이름을 넣어 보며 누가 있는지 훑는 창구가 됩니다.

    **자기가 지금 쓰는 이름은 `true`** 입니다. 고치다가 원래 이름으로 되돌렸을 때
    "다른 분이 쓰고 있어요" 가 뜨면 사용자는 그것을 오류로 읽습니다.

    빈 이름은 여기까지 오지 않습니다 (`min_length=1` → 422). 앱이 모양 검사를 먼저 하고
    통과한 것만 물어보게 되어 있어서, 여기 오는 값은 이미 한 번 걸러진 것입니다.
    """
    trimmed = value.strip()
    if not trimmed:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "닉네임은 비울 수 없습니다."
        )

    row = await app_user_repo.get_by_id(session, user.app_user_id)
    if row is None or row.status != "active":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "다시 로그인해 주세요.")

    if trimmed.lower() == (row.nickname or "").lower():
        return NicknameAvailability(available=True)

    taken = await app_user_repo.is_nickname_taken(session, trimmed)
    return NicknameAvailability(available=not taken)


def _to_me(row: AppUser) -> AppMeResponse:
    return AppMeResponse(
        app_user_id=row.id,
        kakao_id=row.kakao_id,
        # 복호화는 여기서 합니다. 본인 요청이고 본인 것만 나갑니다.
        email=decrypt(row.email_enc) if row.email_enc else None,
        status=row.status,
        created_at=row.created_at,
        room_name=row.room_name,
        nickname=row.nickname,
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

    **돌보미가 남은 강아지가 있으면 409 입니다** (docs/co-care.md §3). 막는 것이
    목적이 아니라 순서를 요구하는 것이라, 메시지에 두 출구를 같이 안내합니다 —
    ⓐ 대표를 넘기고 탈퇴 ⓑ 돌보미를 내보내고 탈퇴(그러면 지금처럼 강아지도 같이
    파기됩니다). 돌보미로만 참여 중인 사람은 이 검사에 걸리지 않고 그냥 나갑니다.
    """
    try:
        await app_auth_service.withdraw(session, app_user_id=user.app_user_id)
    except pet_service.OwnerHasCarersError as exc:
        names = ", ".join(exc.pet_names)
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{names}의 공동 보호자가 남아 있습니다. 대표를 넘기거나 보호자를 "
            f"내보낸 뒤에 탈퇴할 수 있습니다.",
        ) from None
