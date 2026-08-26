"""core/kakao.py — 카카오 id_token 검증.

**네트워크에 나가지 않습니다.** 카카오 공개키 대신 여기서 만든 RSA 키를 캐시에
꽂아 두고, 그 키로 서명한 가짜 id_token 을 검증합니다.

여기서 지키려는 것은 "잘 되는 경우"가 아니라 **못 믿을 토큰이 통과하지 않는 것**입니다.
특히 `aud` 와 알고리즘 고정 — 둘 다 빠뜨려도 정상 로그인은 멀쩡히 되기 때문에,
테스트가 없으면 뚫린 줄 모릅니다.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from joserfc import jwt
from joserfc.jwk import KeySet, OctKey, RSAKey

from daengs_backend.core import kakao
from daengs_backend.core.kakao import (
    KakaoIdTokenInvalidError,
    KakaoUnavailableError,
    verify_id_token,
)

AUD = "test-rest-api-key"  # conftest.py 가 넣는 DAENGS_KAKAO_REST_API_KEY 와 같아야 합니다
KID = "kakao-test-key"


def _now() -> int:
    return int(datetime.now(UTC).timestamp())


@pytest.fixture
def signing_key() -> RSAKey:
    key = RSAKey.generate_key(2048, parameters={"kid": KID})
    return key


@pytest.fixture(autouse=True)
def _install_keys(signing_key: RSAKey, monkeypatch: pytest.MonkeyPatch) -> None:
    """카카오 공개키 자리에 우리 키를 꽂습니다. 매 테스트마다 새 캐시입니다."""
    cache = kakao._KeyCache()
    cache._keys = KeySet([signing_key])
    cache._fetched_at = datetime.now(UTC)
    monkeypatch.setattr(kakao, "_cache", cache)


def _token(key: RSAKey, claims: dict | None = None, *, alg: str = "RS256") -> str:
    """카카오가 발급한 것처럼 생긴 id_token 을 만듭니다."""
    payload = {
        "iss": kakao.ISSUER,
        "aud": AUD,
        "sub": "1234567890",
        "exp": _now() + 300,
        "iat": _now(),
    }
    payload.update(claims or {})
    return jwt.encode({"alg": alg, "kid": KID}, payload, key)


class TestVerify:
    async def test_왕복(self, signing_key: RSAKey) -> None:
        identity = await verify_id_token(_token(signing_key, {"email": "a@x.com"}))

        assert identity.kakao_id == 1234567890
        assert identity.email == "a@x.com"

    async def test_회원번호는_정수다(self, signing_key: RSAKey) -> None:
        """`app_users.kakao_id` 가 BIGINT 라 문자열이면 안 됩니다."""
        identity = await verify_id_token(_token(signing_key))
        assert isinstance(identity.kakao_id, int)

    async def test_이메일_동의를_안_받으면_None(self, signing_key: RSAKey) -> None:
        assert (await verify_id_token(_token(signing_key))).email is None

    async def test_이메일이_빈_문자열이어도_None(self, signing_key: RSAKey) -> None:
        """빈 문자열을 그대로 두면 blind index 가 만들어져 UNIQUE 가 충돌합니다."""
        identity = await verify_id_token(_token(signing_key, {"email": ""}))
        assert identity.email is None


class TestReject:
    """**여기가 이 파일의 본체입니다.** 아래가 하나라도 통과하면 인증이 뚫립니다."""

    async def test_다른_앱의_토큰은_거부한다(self, signing_key: RSAKey) -> None:
        """`aud` 검증. **서명은 멀쩡한 진짜 카카오 토큰입니다.**

        이걸 통과시키면 다른 카카오 앱에서 받은 id_token 으로 우리 서비스에 계정이
        생깁니다. 그 토큰의 sub 는 그 앱 기준 회원번호라 엉뚱한 새 회원이 됩니다.
        """
        with pytest.raises(KakaoIdTokenInvalidError):
            await verify_id_token(_token(signing_key, {"aud": "somebody-elses-app"}))

    async def test_발급자가_다르면_거부한다(self, signing_key: RSAKey) -> None:
        with pytest.raises(KakaoIdTokenInvalidError):
            await verify_id_token(_token(signing_key, {"iss": "https://evil.example"}))

    async def test_만료되면_거부한다(self, signing_key: RSAKey) -> None:
        with pytest.raises(KakaoIdTokenInvalidError):
            await verify_id_token(_token(signing_key, {"exp": _now() - 10}))

    async def test_다른_키로_서명하면_거부한다(self) -> None:
        other = RSAKey.generate_key(2048, parameters={"kid": KID})
        with pytest.raises(KakaoIdTokenInvalidError):
            await verify_id_token(_token(other))

    async def test_HS256_으로_바꿔치면_거부한다(self, signing_key: RSAKey) -> None:
        """**알고리즘 혼동 공격.**

        공격자가 alg 를 HS256 으로 바꾸고 **공개키를 HMAC 비밀키 삼아** 서명하면,
        검증하는 쪽이 같은 공개키로 검증해서 통과시킵니다. 공개키는 누구나
        가져올 수 있으므로 이건 서명 없이 토큰을 만드는 것과 같습니다.
        `JWSRegistry(algorithms=["RS256"])` 가 막습니다.
        """
        public_pem = signing_key.as_pem(private=False)
        forged = jwt.encode(
            {"alg": "HS256", "kid": KID},
            {
                "iss": kakao.ISSUER,
                "aud": AUD,
                "sub": "1234567890",
                "exp": _now() + 300,
            },
            OctKey.import_key(public_pem),
        )
        with pytest.raises(KakaoIdTokenInvalidError):
            await verify_id_token(forged)

    async def test_필수_클레임이_빠지면_거부한다(self, signing_key: RSAKey) -> None:
        for missing in ("iss", "aud", "sub", "exp"):
            claims = {
                "iss": kakao.ISSUER,
                "aud": AUD,
                "sub": "1",
                "exp": _now() + 300,
            }
            del claims[missing]
            token = jwt.encode({"alg": "RS256", "kid": KID}, claims, signing_key)
            with pytest.raises(KakaoIdTokenInvalidError):
                await verify_id_token(token)

    async def test_sub_가_회원번호가_아니면_거부한다(self, signing_key: RSAKey) -> None:
        with pytest.raises(KakaoIdTokenInvalidError):
            await verify_id_token(_token(signing_key, {"sub": "not-a-number"}))

    async def test_빈_토큰과_쓰레기값은_거부한다(self) -> None:
        for junk in ["", "not-a-token", "a.b.c"]:
            with pytest.raises(KakaoIdTokenInvalidError):
                await verify_id_token(junk)

    async def test_nonce_가_다르면_거부한다(self, signing_key: RSAKey) -> None:
        token = _token(signing_key, {"nonce": "from-kakao"})
        with pytest.raises(KakaoIdTokenInvalidError):
            await verify_id_token(token, expected_nonce="what-the-app-sent")

    async def test_nonce_를_기대했는데_없으면_거부한다(
        self, signing_key: RSAKey
    ) -> None:
        with pytest.raises(KakaoIdTokenInvalidError):
            await verify_id_token(_token(signing_key), expected_nonce="something")


class TestKeyCache:
    async def test_공개키를_못_받아오면_Unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**401 이 아닙니다.** 사용자가 다시 로그인해도 똑같이 실패합니다."""
        monkeypatch.setattr(kakao, "_cache", kakao._KeyCache())

        async def boom(*args: object, **kwargs: object) -> None:
            raise httpx.ConnectError("no network")

        monkeypatch.setattr(httpx.AsyncClient, "get", boom)

        with pytest.raises(KakaoUnavailableError):
            await verify_id_token("anything")

    async def test_모르는_kid_면_한_번_다시_받아온다(
        self, signing_key: RSAKey, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """카카오가 키를 갈면 캐시에 없는 kid 가 옵니다. 영원히 캐시하면
        그 순간부터 전원이 로그인 불가가 됩니다."""
        stale = RSAKey.generate_key(2048, parameters={"kid": "old-key"})
        cache = kakao._KeyCache()
        cache._keys = KeySet([stale])
        # 재요청 최소 간격에 걸리지 않도록 오래전에 받아온 것으로 둡니다.
        cache._fetched_at = datetime.now(UTC) - timedelta(minutes=10)

        fetched = 0

        async def fake_fetch() -> KeySet:
            nonlocal fetched
            fetched += 1
            return KeySet([signing_key])

        monkeypatch.setattr(cache, "_fetch", fake_fetch)
        monkeypatch.setattr(kakao, "_cache", cache)

        identity = await verify_id_token(_token(signing_key))

        assert identity.kakao_id == 1234567890
        assert fetched == 1, "JWKS 를 정확히 한 번만 다시 받아와야 합니다"
