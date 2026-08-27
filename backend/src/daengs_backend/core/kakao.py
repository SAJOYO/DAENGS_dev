"""카카오 `id_token`(OIDC) 검증. **우리 토큰과 헷갈리지 마세요.**

    core/token.py   우리가 발급한 access token 을 우리 키로 검증합니다 (JWE, 대칭키)
    core/kakao.py   카카오가 발급한 id_token 을 카카오 공개키로 검증합니다 (JWS, RS256)

여기서 하는 일은 **"이 사람이 진짜 카카오로 로그인했는가"** 하나뿐입니다.
회원을 만들지도, 우리 토큰을 발급하지도 않습니다 — 그건 services/app_auth.py 입니다.

`services/` 가 아니라 `core/` 에 있는 이유는 password.py · crypto.py · token.py 와 같은
성격이기 때문입니다. 값을 받아 **믿어도 되는지만** 판정하고, DB 도 HTTP 요청/응답도
모릅니다. 다른 점은 검증에 필요한 공개키를 네트워크로 받아온다는 것뿐입니다.

**access token 방식을 쓰지 않는 이유** (D-017): 그쪽은 로그인마다 카카오 API 를 부르고,
"이 토큰이 우리 앱 것인가"를 `/v1/user/access_token_info` 로 따로 확인해야 합니다.
그걸 빠뜨리면 **다른 카카오 앱에서 받은 토큰으로도 우리 서비스에 계정이 생깁니다.**
id_token 은 `aud` 검증이 그 역할을 대신합니다.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from joserfc import jwt
from joserfc.errors import ExpiredTokenError, InvalidKeyIdError, JoseError
from joserfc.jwk import KeySet
from joserfc.jws import JWSRegistry

from daengs_backend.config import settings

logger = logging.getLogger(__name__)

__all__ = [
    "KakaoIdTokenError",
    "KakaoIdTokenInvalidError",
    "KakaoIdentity",
    "KakaoUnavailableError",
    "verify_id_token",
]

#: 카카오 인증 서버. id_token 의 `iss` 가 정확히 이 값이어야 합니다.
ISSUER = "https://kauth.kakao.com"

#: 서명 공개키. `openid-configuration` 의 `jwks_uri` 와 같은 값입니다.
JWKS_URL = f"{ISSUER}/.well-known/jwks.json"

# 공개키를 얼마나 들고 있을지.
#
# 짧으면 카카오에 자주 물어보게 되고(로그인 지연 + 카카오 장애에 끌려감),
# 길면 키를 갈았을 때 그만큼 로그인이 막힙니다. 다만 아래 '모르는 kid' 처리가 있어서
# 이 시간이 지나기 전에도 새 키를 받아올 수 있습니다.
_CACHE_TTL = timedelta(hours=6)

# 모르는 kid 를 봤다고 곧바로 다시 받아오면, 엉터리 kid 를 계속 보내는 것만으로
# 우리 서버가 카카오를 두드리게 만들 수 있습니다. 최소 간격을 둡니다.
_REFETCH_MIN_INTERVAL = timedelta(minutes=1)

_HTTP_TIMEOUT = httpx.Timeout(5.0)

# 시계 오차 허용치.
#
# **없으면 우리 시계가 카카오보다 1초만 뒤처져도 모든 로그인이 실패합니다.**
# id_token 의 `iat`(발급 시각)가 우리 기준으로 미래가 되어 "미래에 발급된 토큰"으로
# 거부되기 때문입니다. 실제로 개발 PC 에서 이 상태를 만났고, 에러가
# "우리 앱의 것이 아님"으로 보여서 aud 를 한참 들여다봤습니다.
#
# 서버도 같은 Windows PC 라 똑같이 겪습니다. NTP 동기화가 답이지만, 동기화가
# 잠깐 어긋난다고 로그인이 통째로 죽으면 안 됩니다.
#
# 60초는 `exp`(만료) 쪽에도 같이 적용됩니다 — 만료된 토큰을 1분 더 받아 주는 셈인데,
# 카카오 id_token 은 로그인 직후 한 번만 쓰고 버리는 값이라 위험이 거의 없습니다.
# 우리가 발급하는 access token(core/token.py)에는 이 여유를 주지 않습니다.
_CLOCK_SKEW_LEEWAY = 60

# **알고리즘을 RS256 하나로 못박습니다.**
#
# 열어 두면 알고리즘 혼동 공격이 열립니다 — 공격자가 헤더의 alg 를 HS256 으로 바꾸고
# **공개키를 HMAC 비밀키 삼아** 서명하면, 검증하는 쪽이 같은 공개키로 검증해 통과시킵니다.
# 공개키는 말 그대로 공개라 누구나 가져올 수 있습니다.
# 카카오도 RS256 하나만 씁니다 (openid-configuration 확인).
_REGISTRY = JWSRegistry(algorithms=["RS256"])


class KakaoIdTokenError(Exception):
    """id_token 을 받아들일 수 없습니다. 아래 둘의 부모입니다."""


class KakaoIdTokenInvalidError(KakaoIdTokenError):
    """토큰이 위조되었거나, 만료되었거나, 우리 앱 것이 아닙니다.

    **클라이언트 잘못입니다 → 401.** 만료도 여기에 넣습니다. 우리 access token 과 달리
    이건 '재발급하고 재시도'가 아니라 **카카오 로그인을 다시 해야** 하는 것이라,
    클라이언트가 할 일이 어차피 같습니다.
    """


class KakaoUnavailableError(KakaoIdTokenError):
    """카카오에서 공개키를 못 받아왔습니다.

    **우리 잘못도 클라이언트 잘못도 아닙니다 → 503.** 401 로 주면 앱이 '로그인 실패'로
    알아듣고 사용자에게 다시 로그인하라고 하는데, 다시 해도 똑같이 실패합니다.
    """


@dataclass(frozen=True)
class KakaoIdentity:
    """검증을 통과한 id_token 에서 꺼낸 것. **여기 있는 값만 믿을 수 있습니다.**

    앱이 같이 보낸 다른 값(닉네임, 이메일 등)은 앱이 지어낼 수 있으므로 쓰지 마세요.
    """

    #: 카카오 회원번호. `sub` 클레임이며 `app_users.kakao_id` (BIGINT) 로 갑니다.
    kakao_id: int
    #: 이메일. **동의를 안 받았으면 None 입니다** (필수 동의로 두지 않았습니다).
    email: str | None
    #: 앱이 로그인 요청에 nonce 를 넣었으면 그 값. 안 넣었으면 None.
    nonce: str | None


class _KeyCache:
    """카카오 공개키 캐시. 프로세스마다 하나입니다.

    락을 두는 이유는 **동시에 들어온 첫 로그인들**입니다. 캐시가 비어 있을 때 요청이
    열 개 들어오면 JWKS 를 열 번 받아옵니다. 하나만 받아오고 나머지는 기다립니다.
    """

    def __init__(self) -> None:
        self._keys: KeySet | None = None
        self._fetched_at: datetime | None = None
        self._lock = asyncio.Lock()

    def _is_fresh(self, now: datetime) -> bool:
        return (
            self._keys is not None
            and self._fetched_at is not None
            and now - self._fetched_at < _CACHE_TTL
        )

    async def _fetch(self) -> KeySet:
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                response = await client.get(JWKS_URL)
                response.raise_for_status()
                return KeySet.import_key_set(response.json())
        except (httpx.HTTPError, ValueError, JoseError) as exc:
            # 네트워크 실패 · 5xx · JSON 이 아님 · 키 형식이 이상함.
            # 전부 "지금은 검증할 수 없다"로 같습니다.
            logger.warning("카카오 JWKS 를 받아오지 못했습니다: %s", exc)
            raise KakaoUnavailableError("카카오 공개키를 받아오지 못했습니다.") from exc

    async def get(self, *, force: bool = False) -> KeySet:
        """캐시된 공개키. `force` 면 (최소 간격을 지켜) 다시 받아옵니다."""
        if not force and self._is_fresh(datetime.now(UTC)):
            return self._keys  # type: ignore[return-value]

        async with self._lock:
            # 락을 기다리는 동안 다른 요청이 이미 받아왔을 수 있습니다.
            now = datetime.now(UTC)
            if self._is_fresh(now) and not force:
                return self._keys  # type: ignore[return-value]

            if (
                force
                and self._keys is not None
                and self._fetched_at is not None
                and now - self._fetched_at < _REFETCH_MIN_INTERVAL
            ):
                # 방금 받아왔는데 또 모르는 kid 입니다. 카카오가 키를 간 게 아니라
                # 엉터리 토큰일 가능성이 큽니다. 두드리지 않고 있는 키로 갑니다.
                return self._keys

            self._keys = await self._fetch()
            self._fetched_at = datetime.now(UTC)
            logger.info("카카오 JWKS 를 갱신했습니다 (키 %d개)", len(self._keys.keys))
            return self._keys


_cache = _KeyCache()


def _claims_registry() -> jwt.JWTClaimsRegistry:
    """검증할 클레임. **`aud` 가 이 카드의 핵심입니다.**

    `aud` 를 안 보면 다른 카카오 앱에서 발급된 id_token 도 서명은 멀쩡하게 통과합니다.
    그 토큰의 `sub` 는 그 앱 기준의 회원번호라, 우리 DB 에는 **엉뚱한 새 회원**이
    생깁니다. 서명 검증만으로 끝났다고 생각하기 쉬운 자리입니다.

    매번 만드는 이유는 `settings` 를 import 시점이 아니라 호출 시점에 읽기 위해서입니다
    (테스트가 키를 바꿔 끼울 수 있어야 합니다).
    """
    return jwt.JWTClaimsRegistry(
        leeway=_CLOCK_SKEW_LEEWAY,
        iss={"essential": True, "value": ISSUER},
        aud={"essential": True, "value": settings.kakao_rest_api_key},
        sub={"essential": True},
        exp={"essential": True},
    )


async def verify_id_token(
    token: str, *, expected_nonce: str | None = None
) -> KakaoIdentity:
    """카카오 id_token 을 검증하고 신원을 꺼냅니다.

    실패하면 예외입니다. **None 을 돌려주지 않습니다** — '검증 실패'가 '값 없음'으로
    뭉개지면 통과시키는 실수가 나옵니다 (core/token.py 와 같은 이유).

    `expected_nonce` 를 주면 토큰 안의 nonce 와 대조합니다. 앱이 로그인 요청에 nonce 를
    넣었을 때만 쓸 수 있고, 넣지 않았다면 이 인자도 주지 마세요.
    """
    if not token:
        raise KakaoIdTokenInvalidError("id_token 이 비어 있습니다.")

    keys = await _cache.get()
    try:
        decoded = jwt.decode(token, keys, registry=_REGISTRY)
    except InvalidKeyIdError:
        # 캐시에 없는 kid 입니다. 카카오가 키를 갈았을 수 있으니 **한 번만** 다시
        # 받아서 재시도합니다. 영원히 캐시하면 키 교체 때 전원이 로그인 불가가 되고,
        # 매번 받아오면 카카오에 끌려다닙니다.
        #
        # **이 예외는 JoseError 의 갈래입니다.** 아래 `except (JoseError, ValueError)`
        # 보다 반드시 먼저 와야 하고, KeyError 로 잡으면 안 됩니다 — 그렇게 두면
        # 재요청 경로를 영영 안 타서 키 교체 때 조용히 전원 로그인 불가가 됩니다.
        logger.info("모르는 kid 입니다. JWKS 를 다시 받아옵니다.")
        keys = await _cache.get(force=True)
        try:
            decoded = jwt.decode(token, keys, registry=_REGISTRY)
        except (JoseError, ValueError) as retry_exc:
            raise KakaoIdTokenInvalidError(
                "id_token 서명을 확인할 수 없습니다."
            ) from retry_exc
    except ExpiredTokenError as exc:
        raise KakaoIdTokenInvalidError("id_token 이 만료되었습니다.") from exc
    except (JoseError, ValueError) as exc:
        # 서명 불일치 · 형식 오류 · 허용하지 않는 alg.
        raise KakaoIdTokenInvalidError("id_token 을 신뢰할 수 없습니다.") from exc

    try:
        _claims_registry().validate(decoded.claims)
    except ExpiredTokenError as exc:
        raise KakaoIdTokenInvalidError("id_token 이 만료되었습니다.") from exc
    except JoseError as exc:
        # iss 가 다르거나, **aud 가 우리 앱이 아니거나**, 필수 클레임이 없습니다.
        logger.warning("id_token 클레임 검증 실패: %s", exc)
        raise KakaoIdTokenInvalidError("id_token 이 우리 앱의 것이 아닙니다.") from exc

    claims = decoded.claims

    if expected_nonce is not None and claims.get("nonce") != expected_nonce:
        # 가로챈 id_token 을 다시 쓰는 것을 막는 장치입니다.
        logger.warning("id_token 의 nonce 가 맞지 않습니다.")
        raise KakaoIdTokenInvalidError("nonce 가 맞지 않습니다.")

    try:
        kakao_id = int(claims["sub"])
    except (TypeError, ValueError) as exc:
        # 카카오가 만든 토큰이면 여기 올 수 없습니다.
        raise KakaoIdTokenInvalidError("sub 가 회원번호가 아닙니다.") from exc

    return KakaoIdentity(
        kakao_id=kakao_id,
        # 동의를 안 받았으면 클레임이 아예 없습니다. 빈 문자열도 None 으로 봅니다 —
        # blind index 에 빈 문자열이 들어가면 미동의 회원끼리 UNIQUE 충돌합니다.
        email=claims.get("email") or None,
        nonce=claims.get("nonce"),
    )
