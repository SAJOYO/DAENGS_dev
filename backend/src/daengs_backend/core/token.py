"""access token(JWE) 발급·검증과 refresh token 생성 (D-015).

두 토큰의 성격이 정반대입니다.

    access   JWE. 필요한 것을 토큰이 들고 다니고 **DB 를 보지 않습니다.** 5분.
             빠른 대신 발급하고 나면 취소할 수 없어서, 수명을 짧게 잡아 막습니다.
    refresh  뜻이 없는 난수. DB 에 SHA-256 해시만 두고 쓸 때마다 조회합니다. 7일.
             느린 대신 언제든 끊을 수 있습니다 — 강제 로그아웃이 이것 때문에 됩니다.

여기는 **만들고 푸는 것만** 합니다. DB 도 HTTP 도 모릅니다.
"이 refresh 가 아직 살아 있나"를 판단하는 것은 services/auth.py 입니다.
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from joserfc import jwt
from joserfc.errors import ExpiredTokenError, JoseError
from joserfc.jwe import JWERegistry
from joserfc.jwk import OctKey

from daengs_backend.config import settings
from daengs_backend.core.keys import load_key

__all__ = [
    "ACCESS_TTL",
    "REFRESH_REUSE_GRACE",
    "REFRESH_TTL",
    "AccessClaims",
    "TokenError",
    "TokenExpiredError",
    "TokenInvalidError",
    "create_access_token",
    "decode_access_token",
    "generate_refresh_token",
    "hash_refresh_token",
]

# ── 수명 ──────────────────────────────────────────────────────────────
# 이 카드의 시간 상수는 전부 여기 모아 둡니다. 흩어 두면 하나만 고치게 됩니다.

# access 는 취소가 안 되므로 짧아야 합니다. 계정을 정지시켜도 이미 나간 토큰은
# 이 시간만큼 살아 있습니다 — 그 구멍의 크기가 이 값입니다.
ACCESS_TTL = timedelta(minutes=5)

# 회전해도 이 값으로 **연장하지 않습니다**. 처음 로그인한 시각 기준의 절대 만료라,
# 계속 쓰더라도 7일 뒤에는 반드시 다시 로그인합니다 (services/auth.py 가 지킵니다).
REFRESH_TTL = timedelta(days=7)

# 폐기된 refresh 가 이 시간 안에 다시 오면 탈취가 아니라 '탭 경합'으로 봅니다.
# 한 브라우저의 탭 두 개가 같은 쿠키로 동시에 재발급을 시도하면 한쪽은 반드시
# 방금 폐기된 토큰을 쓰게 되는데, 그걸 탈취로 오해하면 멀쩡한 세션이 전부 끊깁니다.
# access 가 5분이라 재발급이 잦고, 그만큼 이 경합도 자주 생깁니다.
REFRESH_REUSE_GRACE = timedelta(seconds=10)

# ── JWE ───────────────────────────────────────────────────────────────
# alg="dir"  : 키를 따로 감싸지 않고 우리 대칭키를 그대로 콘텐츠 암호화 키로 씁니다.
#              발급자와 검증자가 같은 서버 하나뿐이라 키를 교환할 상대가 없습니다.
# enc="A256GCM" : GCM 은 AEAD 라 암호화와 위변조 탐지를 같이 합니다.
#              그래서 JWS 로 서명한 뒤 JWE 로 다시 싸는(nested) 구성이 필요 없습니다.
_HEADER = {"alg": "dir", "enc": "A256GCM"}

_KEY = OctKey.import_key(
    load_key("DAENGS_JWE_KEY", settings.jwe_key.get_secret_value())
)

# **이 registry 를 빼먹으면 안 됩니다.** joserfc 의 jwt.encode/decode 는 registry 가
# JWERegistry 일 때만 JWE 로 가고, 아니면 조용히 JWS(서명만) 경로로 빠집니다.
# 그러면 role 이 base64 디코딩만으로 읽히는 토큰이 나가는데 에러는 나지 않습니다.
_REGISTRY = JWERegistry()

# 검증할 때 반드시 있어야 하는 클레임. exp 는 registry 가 시간까지 대조합니다.
_CLAIMS = jwt.JWTClaimsRegistry(
    sub={"essential": True},
    role={"essential": True},
    exp={"essential": True},
)


class TokenError(Exception):
    """토큰을 신뢰할 수 없습니다. 아래 둘의 부모입니다."""


class TokenExpiredError(TokenError):
    """수명이 지났습니다. **정상적인 흐름입니다.**

    5분마다 반드시 일어나므로 경고로 로그를 남기지 마세요.
    라우터는 이걸 받으면 401 을 주고, 클라이언트는 재발급 후 재시도합니다.
    """


class TokenInvalidError(TokenError):
    """형식이 깨졌거나 위조되었거나 우리 키로 열리지 않습니다.

    만료와 나눠 둔 이유는 **뜻이 다르기 때문**입니다. 만료는 시계가 흐른 것뿐이지만,
    이쪽은 누가 토큰을 손댔다는 뜻입니다. 응답은 똑같이 401 이어도 로그는 달라야 합니다.
    (core/password.py 가 '틀림'과 '해시 깨짐'을 나눠 둔 것과 같은 이유입니다)
    """


@dataclass(frozen=True)
class AccessClaims:
    """access token 에서 꺼낸 것. **DB 를 거치지 않은 값입니다.**

    최대 ACCESS_TTL 만큼 낡을 수 있습니다 — role 을 바꾸거나 계정을 정지시켜도
    이미 나간 토큰에는 옛 값이 들어 있습니다. 지금 DB 상태가 필요하면
    (예: `GET /auth/me`) 이걸 믿지 말고 admin_id 로 다시 조회하세요.
    """

    admin_id: uuid.UUID
    role: str
    expires_at: datetime


def create_access_token(admin_id: uuid.UUID, role: str) -> str:
    """access token 을 만듭니다. 5분짜리입니다.

    role 을 넣는 이유는 권한 검사마다 DB 를 보지 않기 위해서입니다. 그게 무상태
    토큰을 쓰는 이유 전부이고, 대가로 role 변경이 최대 5분 늦게 반영됩니다.
    """
    now = datetime.now(UTC)
    expires_at = now + ACCESS_TTL
    claims = {
        "sub": str(admin_id),
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    return jwt.encode(_HEADER, claims, _KEY, registry=_REGISTRY)


def decode_access_token(token: str) -> AccessClaims:
    """access token 을 풉니다. 못 믿을 토큰이면 TokenError 를 냅니다.

    **예외를 삼키고 None 을 돌려주지 마세요.** 만료·위조·형식오류가 전부 None 이 되면
    호출하는 쪽이 구분을 못 하고, 무엇보다 '검증에 실패했다'가 '값이 없다'로
    뭉개져서 통과시키는 실수가 나옵니다.
    """
    try:
        decoded = jwt.decode(token, _KEY, registry=_REGISTRY)
        _CLAIMS.validate(decoded.claims)
    except JoseError as exc:
        # joserfc 는 만료도 JoseError 갈래(ExpiredTokenError)로 냅니다.
        # 타입 이름이 아니라 클래스로 갈라야 오타에 안 걸립니다.
        if isinstance(exc, ExpiredTokenError):
            raise TokenExpiredError("access token 이 만료되었습니다.") from exc
        raise TokenInvalidError("access token 을 신뢰할 수 없습니다.") from exc
    except ValueError as exc:
        # base64 가 아니거나 점 개수가 안 맞는 등, joserfc 가 파싱 전에 내는 것들.
        raise TokenInvalidError("access token 형식이 올바르지 않습니다.") from exc

    claims = decoded.claims
    try:
        admin_id = uuid.UUID(claims["sub"])
    except (ValueError, AttributeError, TypeError) as exc:
        # 우리가 만든 토큰이면 여기 올 수 없습니다. 왔다면 키가 새어 나가
        # 남이 만든 토큰이라는 뜻이라, 통과시키면 안 됩니다.
        raise TokenInvalidError("sub 가 UUID 가 아닙니다.") from exc

    return AccessClaims(
        admin_id=admin_id,
        role=claims["role"],
        expires_at=datetime.fromtimestamp(claims["exp"], UTC),
    )


def generate_refresh_token() -> str:
    """refresh token 원문. 32바이트 난수를 urlsafe base64 로 한 43자입니다.

    **뜻이 없는 문자열입니다.** 안에 admin_id 도 만료도 들어 있지 않습니다 —
    그 정보는 전부 DB 행에 있고, 이 값은 그 행을 찾는 열쇠일 뿐입니다.
    그래서 서버가 행을 지우거나 revoked_at 을 찍는 것만으로 즉시 무효가 됩니다.

    secrets 를 씁니다. random 모듈은 예측 가능해서 토큰에 쓰면 안 됩니다.
    """
    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> str:
    """DB 에 저장할 SHA-256 hex 64자. 원문은 어디에도 저장하지 않습니다.

    비밀번호와 달리 Argon2 가 아니라 SHA-256 인 이유는 두 가지입니다 —
    이 값은 서버가 만든 고엔트로피 난수라 사전 공격 대상이 아니고,
    재발급마다 조회해야 해서 빨라야 합니다. (03_auth.sql 의 token_hash 주석과 같은 내용)

    salt 를 넣지 않는 것도 같은 이유입니다. 넣으면 해시로 행을 찾을 수 없습니다.
    """
    return hashlib.sha256(token.encode()).hexdigest()
