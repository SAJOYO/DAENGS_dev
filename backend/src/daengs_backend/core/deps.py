"""FastAPI 의존성 — "지금 누구인가"와 "이걸 해도 되는가".

**엔드포인트는 role 이 아니라 권한을 선언합니다.**

    @router.get("/users")
    async def list_users(admin: Annotated[Principal, Depends(require(Perm.PII_READ))]):

`if admin.role == "ADMIN" or admin.role == "OPERATOR"` 처럼 쓰지 마세요. role 구성을
바꿀 때마다 코드 전체를 뒤지게 됩니다. 아래 ROLE_PERMISSIONS 한 곳만 고치면 되도록
한 겹을 둡니다.

**토큰이 누구 것인지부터 봅니다.** 발급기(`core/token.py`)는 앱 회원 토큰도 같은
키로 만들기 때문에, 종류를 확인하지 않으면 앱 회원이 관리자 API 에 그대로 들어옵니다.
`sub` 는 어느 쪽이든 UUID 한 개라 그것만으로는 구분되지 않습니다.

관리자 의존성은 DB 를 보지 않습니다. 앱 회원 의존성은 예외입니다 — 탈퇴 뒤에도
access token 서명은 최대 ACCESS_TTL(5분) 유효하므로, user-owned API 공통 경계에서
active 상태와 탈퇴 동시 쓰기를 DB 잠금으로 확인합니다.

그 예외의 예외가 `current_app_member_token_only` 입니다. 서비스가 **자기 짧은 TX 를
따로 여는** 엔드포인트(AI 요약처럼 외부 호출을 사이에 둔 것)는 요청 수명 세션과
그 안의 `app_users FOR UPDATE` 를 가지면 안 됩니다 — 요청 TX 가 잠근 행을 서비스의
두 번째 TX 가 FK 로 다시 기다리면 서로를 기다리는 자기 교착이 됩니다 (2026-09-03
서버 Phase 3A 에서 `POST /app/chats/{id}/summary` 가 그렇게 영영 멈췄습니다).
"""

import logging
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from daengs_backend.core.database import get_session
from daengs_backend.core.subject import SubjectType
from daengs_backend.core.token import (
    AccessClaims,
    TokenExpiredError,
    TokenInvalidError,
    decode_access_token,
)
from daengs_backend.repositories import app_user as app_user_repo

logger = logging.getLogger(__name__)

#: access token 을 담는 쿠키 이름. routers/auth.py 가 굽는 이름과 같아야 합니다.
ACCESS_COOKIE = "daengs_access"


class Perm(StrEnum):
    """할 수 있는 일. role 이 아니라 이것으로 엔드포인트를 지킵니다.

    이름은 "무엇을 하는가"로 짓습니다. `IS_ADMIN` 같은 이름을 넣으면 결국
    role 비교로 되돌아갑니다.
    """

    #: 관리자 계정 발급·정지·권한 변경. ADMIN 만 가집니다.
    ADMIN_MANAGE = "admin:manage"
    #: 개인정보 복호화 (app_users 의 *_enc 를 원문으로 보기).
    PII_READ = "pii:read"
    #: 운영 데이터 쓰기.
    OPS_WRITE = "ops:write"
    #: 지식베이스 문서 쓰기.
    KB_WRITE = "kb:write"
    #: 검색 품질 점검.
    SEARCH_INSPECT = "search:inspect"
    #: 지표 조회.
    METRICS_READ = "metrics:read"
    #: 기본 조회. 로그인한 사람은 전부 가집니다 (개인정보는 마스킹).
    READ = "read"


# role → 가진 권한. **03_auth.sql 의 role 주석과 같은 내용입니다.**
# 한쪽만 고치면 어긋나므로 같이 고치세요 (D-014 로 ENUM 이 아닌 CHECK 를 쓴 이유이기도 합니다).
#
# 지금 실제로 발급하는 것은 ADMIN 하나이고 나머지 넷은 자리만 잡아 둔 상태입니다.
ROLE_PERMISSIONS: dict[str, frozenset[Perm]] = {
    # 계정·권한 관리까지 전부.
    "ADMIN": frozenset(Perm),
    # 운영 CRUD + 개인정보 복호화. 계정 관리만 빠집니다.
    "OPERATOR": frozenset(Perm) - {Perm.ADMIN_MANAGE},
    # 지식베이스와 검색 점검. 개인정보 접근이 없습니다.
    "CURATOR": frozenset(
        {Perm.KB_WRITE, Perm.SEARCH_INSPECT, Perm.METRICS_READ, Perm.READ}
    ),
    # 지표·검색 점검 조회. 쓰기가 없습니다.
    "ANALYST": frozenset({Perm.SEARCH_INSPECT, Perm.METRICS_READ, Perm.READ}),
    # 조회만. 개인정보는 마스킹해서 보여 줍니다.
    "VIEWER": frozenset({Perm.READ}),
}


@dataclass(frozen=True)
class Principal:
    """지금 요청을 보낸 관리자. **토큰에서 나온 값이라 DB 를 거치지 않았습니다.**

    최대 ACCESS_TTL 만큼 낡을 수 있습니다. 화면에 뿌릴 이름이나 최신 role 이
    필요하면 `GET /auth/me` 처럼 DB 를 다시 읽으세요.
    """

    admin_id: uuid.UUID
    role: str

    @property
    def permissions(self) -> frozenset[Perm]:
        # 모르는 role 이면 아무 권한도 주지 않습니다. DB 의 CHECK 제약이 막고 있지만,
        # 만약 뚫렸다면 "권한 없음"으로 떨어지는 쪽이 안전합니다.
        return ROLE_PERMISSIONS.get(self.role, frozenset())

    def can(self, perm: Perm) -> bool:
        return perm in self.permissions


def _extract_token(request: Request) -> str | None:
    """쿠키를 먼저 보고, 없으면 Authorization 헤더를 봅니다.

    둘을 다 받는 이유는 전달 방식만 다르고 **토큰은 하나를 공유**하기 때문입니다.
    관리자 웹은 httpOnly 쿠키(JS 가 못 읽어서 XSS 에 덜 취약), 네이티브 앱은
    쿠키 저장소가 없으니 Bearer 헤더입니다.
    """
    token = request.cookies.get(ACCESS_COOKIE)
    if token:
        return token

    header = request.headers.get("authorization")
    if header and header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return None


def _claims_of(request: Request) -> AccessClaims:
    """요청에서 access token 을 꺼내 풉니다. 못 믿을 토큰이면 401 입니다.

    아래 세 개의 문(`current_admin` · `current_app_user` · `admin_or_app_user`)이
    공유합니다. **"누구인가"까지만 하고 "들어와도 되는가"는 하지 않습니다** —
    종류를 어디까지 받을지는 부르는 쪽마다 다릅니다.

    만료와 위조를 **응답에서 구분하지 않습니다** — 클라이언트가 할 일은 둘 다
    "재발급하고 다시" 로 같습니다. 다만 로그는 구분합니다. 만료는 5분마다 일어나는
    정상이고, 위조는 누가 토큰을 손댔다는 뜻입니다.
    """
    token = _extract_token(request)
    if token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "인증이 필요합니다.")

    try:
        return decode_access_token(token)
    except TokenExpiredError:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "인증이 만료되었습니다."
        ) from None
    except TokenInvalidError:
        # 여기는 시끄러워야 합니다. 정상 흐름에서는 나올 수 없는 값입니다.
        logger.warning("신뢰할 수 없는 access token (ip=%s)", _client_host(request))
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "인증이 필요합니다."
        ) from None


async def current_admin(request: Request) -> Principal:
    """access token 을 풀어 Principal 로. 못 믿을 토큰이면 401 입니다.

    토큰을 푸는 것 자체는 `_claims_of` 가 합니다. 여기서 보는 것은 **그게 관리자
    것이냐** 하나뿐입니다.
    """
    claims = _claims_of(request)

    if claims.subject_type is not SubjectType.ADMIN:
        # 진짜 우리 토큰이지만 **관리자 것이 아닙니다.** 앱 회원이 자기 토큰으로
        # 관리자 API 를 부른 것이라, 만료와 달리 재발급해도 달라지지 않습니다.
        #
        # 그래도 401 입니다 (403 이 아닙니다) — 403 은 "누구인지는 맞는데 권한이
        # 모자라다"는 뜻이고, 여기는 애초에 이 문으로 들어올 사람이 아닙니다.
        # 응답 메시지도 인증 실패와 똑같이 둡니다. "당신은 앱 회원이군요"를
        # 알려 줄 이유가 없습니다.
        logger.warning(
            "관리자 API 에 %s 토큰 (subject=%s, ip=%s)",
            claims.subject_type.value,
            claims.subject_id,
            _client_host(request),
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "인증이 필요합니다.")

    # decode_access_token 이 관리자 토큰에 role 이 있는 것을 보장합니다.
    assert claims.role is not None
    return Principal(admin_id=claims.subject_id, role=claims.role)


CurrentAdmin = Annotated[Principal, Depends(current_admin)]


@dataclass(frozen=True)
class AppPrincipal:
    """지금 요청을 보낸 앱 회원. **role 이 없습니다.**

    앱 회원은 자기 것만 봅니다. `current_app_user` 가 만든 것이면 DB 에서 active
    상태도 확인한 것이고, `current_app_member_token_only` 나 `admin_or_app_user` 가 만든
    것이면 **토큰만 본 것**입니다 — 그 경우 서비스가 자기 TX 에서 다시 확인합니다.
    권한 등급이 필요한 화면이 없어서 `Perm` 도, `require(...)` 도 쓰지 않습니다. "남의 것을 보려 하는가"는 각 엔드포인트가
    `app_user_id` 로 직접 확인합니다.
    """

    app_user_id: uuid.UUID


def _app_claims_of(request: Request) -> AccessClaims:
    """앱 회원 토큰만 받습니다. 관리자 토큰은 만료·위조와 같은 401 입니다.

    `current_app_user` 와 `current_app_member_token_only` 가 공유하는 문입니다 —
    "앱 회원 토큰인가" 까지만 하고, active 상태를 DB 에서 볼지는 부르는 쪽이 정합니다.
    """
    claims = _claims_of(request)

    if claims.subject_type is not SubjectType.APP:
        # `current_admin` 의 거울상입니다. 관리자 토큰의 `sub` 는 `admin_users` 의 UUID 라
        # `app_users` 에서 조회하면 없는 회원이 됩니다. 그 자리를 404 나 500 으로 만나지
        # 말고 여기서 끊습니다. 응답 메시지는 인증 실패와 똑같이 둡니다.
        logger.warning(
            "앱 API 에 %s 토큰 (subject=%s, ip=%s)",
            claims.subject_type.value,
            claims.subject_id,
            _client_host(request),
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "인증이 필요합니다.")
    return claims


async def current_app_user(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AppPrincipal:
    """access token 을 풀어 AppPrincipal 로. 못 믿을 토큰이면 401 입니다.

    `current_admin` 의 거울상입니다 — 저쪽이 앱 회원 토큰을 막듯이, 여기서는
    **관리자 토큰을 막습니다** (`_app_claims_of`). 그리고 요청 세션에서 회원이 아직
    active 인지 잠그며 확인합니다.

    **둘을 함께 받아야 하는 엔드포인트는 `admin_or_app_user` 를 쓰세요.** 이 함수를
    고쳐서 관리자를 통과시키면 그것을 쓰는 앱 API 가 전부 같이 열립니다.

    **서비스가 자기 TX 를 따로 여는 엔드포인트에는 쓰지 마세요** —
    `current_app_member_token_only` 를 보세요.
    """
    claims = _app_claims_of(request)

    # access token 은 무상태라 탈퇴 뒤에도 최대 ACCESS_TTL 동안 서명 자체는 유효합니다.
    # 모든 앱 소유 데이터 API 가 지나는 이 한 곳에서 현재 상태를 확인합니다. FOR UPDATE
    # 잠금은 탈퇴와 동시 쓰기도 직렬화합니다: 먼저 끝난 쓰기는 탈퇴가 지우고, 탈퇴가
    # 먼저 끝났으면 아래 조회가 active 행을 찾지 못합니다.
    user = await app_user_repo.get_active_for_update(session, claims.subject_id)
    if user is None:
        logger.warning(
            "앱 API 에 쓸 수 없는 회원 (subject=%s, ip=%s)",
            claims.subject_id,
            _client_host(request),
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "다시 로그인해 주세요.")

    return AppPrincipal(app_user_id=claims.subject_id)


CurrentAppUser = Annotated[AppPrincipal, Depends(current_app_user)]


async def current_app_member_token_only(request: Request) -> AppPrincipal:
    """앱 회원 access token 을 **토큰만으로** AppPrincipal 로. DB 세션을 열지 않습니다.

    받는 토큰의 범위는 `current_app_user` 와 같습니다 — 없거나·위조·만료·관리자 토큰은
    전부 401 입니다. 다른 것은 하나뿐입니다: **회원이 아직 active 인지 여기서 보지
    않습니다.** 탈퇴 뒤에도 access token 서명은 최대 ACCESS_TTL 동안 유효하므로, 이
    principal 은 "이 토큰의 주인이 누구인가" 까지만 말합니다.

    그래서 **신원만으로는 user-owned 쓰기를 해서는 안 됩니다.** 이 의존성을 쓰는
    엔드포인트의 서비스는 자기 짧은 TX 안에서
    `app_user_repo.get_active_for_update` 로 active 상태를 다시 확인하고(그 잠금이
    탈퇴와 직렬화됩니다) 같은 TX 에서 쓰기를 예약한 뒤 commit·close 해야 합니다.
    아니면 `AppUserNotActiveError` 같은 것을 올려 라우터가 `current_app_user` 와 같은
    401 로 바꿉니다.

    **어디에 쓰나**: 서비스가 트랜잭션 경계를 따로 소유하는 엔드포인트 —
    `POST /app/chats/{id}/summary` 처럼 외부 호출 전후로 짧은 TX 를 여닫는 자리.
    요청 수명 `get_session` 을 같이 받는 보통의 앱 API 는 계속 `CurrentAppUser` 입니다.
    거기서는 이것을 쓰면 active 확인이 빠집니다.

    **왜 따로 있나**: `current_app_user` 는 요청 세션에서 `app_users FOR UPDATE` 를 잡고
    요청이 끝날 때까지 들고 있습니다. 서비스가 그 사이에 두 번째 세션으로
    `chat_summaries` 를 INSERT 하면 FK 가 같은 행의 `FOR KEY SHARE` 를 기다리고, 요청
    TX 는 그 INSERT 를 기다립니다 — 외부 공급자는 불리지도 않은 채 워커 하나와 DB 연결
    둘이 영영 묶입니다 (서버 Phase 3A). 게다가 외부 호출 동안 행 잠금이 살아 있는 것
    자체가 D-048 의 경계 위반입니다.
    """
    claims = _app_claims_of(request)
    return AppPrincipal(app_user_id=claims.subject_id)


#: 토큰만 확인한 앱 회원. **active 여부는 서비스의 짧은 TX 가 다시 봅니다.**
CurrentAppMemberTokenOnly = Annotated[AppPrincipal, Depends(current_app_member_token_only)]


def require(*perms: Perm):
    """이 권한들을 **전부** 가져야 통과하는 의존성을 만듭니다.

        @router.delete("/admins/{admin_id}")
        async def remove(admin: Annotated[Principal, Depends(require(Perm.ADMIN_MANAGE))]):

    **관리자 전용입니다.** 앱 회원 토큰은 `CurrentAdmin` 에서 401 로 막힙니다.
    둘을 함께 받아야 하면 `admin_or_app_user` 를 쓰세요.
    """

    async def dependency(admin: CurrentAdmin) -> Principal:
        _deny_unless_permitted(admin, perms)
        return admin

    return dependency


def admin_or_app_user(*perms: Perm):
    """관리자와 앱 회원을 **둘 다** 받는 문. 관리자에게만 권한을 요구합니다.

        app.include_router(walk.router, dependencies=[Depends(admin_or_app_user(Perm.READ))])

    `current_admin` 과 `current_app_user` 가 서로를 막는 것과 반대로 보이지만, 규칙이
    느슨해진 것이 아니라 **엔드포인트의 성격이 다릅니다.** 여기를 쓸 수 있는 것은
    principal 을 받기만 하고 **쓰지 않는** 엔드포인트뿐입니다 — 지금은 Life 의 직접 API 둘
    (`/life/walk-conditions` · `/life/ask`)이고, 저기는 좌표와 질문만 보고 답합니다.

    **신원으로 남의 것을 걸러야 하는 API 에는 쓰지 마세요.** 관리자 `sub` 는
    `admin_users` 의 UUID 라 `app_users` 에서 조회하면 없는 회원이 되고, 그때
    `current_app_user` 가 막아 주던 404·500 을 그대로 만납니다.

    앱 회원에게는 권한을 묻지 않습니다. `AppPrincipal` 에는 role 이 없습니다 —
    등급이 필요한 화면이 없어서 처음부터 두지 않았습니다.
    """

    async def dependency(request: Request) -> Principal | AppPrincipal:
        claims = _claims_of(request)

        if claims.subject_type is SubjectType.APP:
            return AppPrincipal(app_user_id=claims.subject_id)

        # decode_access_token 이 관리자 토큰에 role 이 있는 것을 보장합니다.
        assert claims.role is not None
        admin = Principal(admin_id=claims.subject_id, role=claims.role)
        _deny_unless_permitted(admin, perms)
        return admin

    return dependency


def _deny_unless_permitted(admin: Principal, perms: tuple[Perm, ...]) -> None:
    """권한이 하나라도 모자라면 403. `require` 와 `admin_or_app_user` 가 공유합니다.

    403 입니다 (401 이 아닙니다). 누구인지는 확인됐고 권한이 모자란 것이라, 다시
    로그인해도 달라지지 않습니다 — 401 을 주면 클라이언트가 재발급을 시도하며
    무한히 돕니다 (`frontend/lib/api.ts`).
    """
    missing = [p for p in perms if not admin.can(p)]
    if not missing:
        return

    logger.info(
        "권한 없음 (admin=%s, role=%s, 필요=%s)",
        admin.admin_id,
        admin.role,
        [p.value for p in missing],
    )
    raise HTTPException(status.HTTP_403_FORBIDDEN, "권한이 없습니다.")


def _client_host(request: Request) -> str:
    return request.client.host if request.client else "unknown"
