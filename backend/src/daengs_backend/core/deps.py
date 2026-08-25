"""FastAPI 의존성 — "지금 누구인가"와 "이걸 해도 되는가".

**엔드포인트는 role 이 아니라 권한을 선언합니다.**

    @router.get("/users")
    async def list_users(admin: Annotated[Principal, Depends(require(Perm.PII_READ))]):

`if admin.role == "ADMIN" or admin.role == "OPERATOR"` 처럼 쓰지 마세요. role 구성을
바꿀 때마다 코드 전체를 뒤지게 됩니다. 아래 ROLE_PERMISSIONS 한 곳만 고치면 되도록
한 겹을 둡니다.

**여기서는 DB 를 보지 않습니다.** access token 을 푸는 것으로 끝입니다 —
그게 무상태 토큰을 쓰는 이유 전부입니다. 대가로 role 변경과 계정 정지가
최대 ACCESS_TTL(5분) 늦게 반영됩니다. 지금 DB 상태가 필요한 곳(`GET /auth/me`)은
admin_id 로 직접 조회하세요.
"""

import logging
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from daengs_backend.core.token import (
    TokenExpiredError,
    TokenInvalidError,
    decode_access_token,
)

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


async def current_admin(request: Request) -> Principal:
    """access token 을 풀어 Principal 로. 못 믿을 토큰이면 401 입니다.

    만료와 위조를 **응답에서는 구분하지 않습니다** — 클라이언트가 할 일은 둘 다
    "재발급하고 다시" 로 같습니다. 다만 로그는 구분합니다. 만료는 5분마다 일어나는
    정상이고, 위조는 누가 토큰을 손댔다는 뜻입니다.
    """
    token = _extract_token(request)
    if token is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "인증이 필요합니다."
        )

    try:
        claims = decode_access_token(token)
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

    return Principal(admin_id=claims.admin_id, role=claims.role)


CurrentAdmin = Annotated[Principal, Depends(current_admin)]


def require(*perms: Perm):  # noqa: ANN201
    """이 권한들을 **전부** 가져야 통과하는 의존성을 만듭니다.

        @router.delete("/admins/{admin_id}")
        async def remove(admin: Annotated[Principal, Depends(require(Perm.ADMIN_MANAGE))]):

    403 입니다 (401 이 아닙니다). 누구인지는 확인됐고 권한이 모자란 것이라,
    다시 로그인해도 달라지지 않습니다 — 401 을 주면 클라이언트가 재발급을
    시도하며 무한히 돕니다.
    """

    async def dependency(admin: CurrentAdmin) -> Principal:
        missing = [p for p in perms if not admin.can(p)]
        if missing:
            logger.info(
                "권한 없음 (admin=%s, role=%s, 필요=%s)",
                admin.admin_id,
                admin.role,
                [p.value for p in missing],
            )
            raise HTTPException(status.HTTP_403_FORBIDDEN, "권한이 없습니다.")
        return admin

    return dependency


def _client_host(request: Request) -> str:
    return request.client.host if request.client else "unknown"
