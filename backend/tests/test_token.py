"""core/token.py — access token(JWE) 과 refresh token.

여기서 지키려는 것은 "잘 되는 경우"가 아니라 **못 믿을 토큰이 통과하지 않는 것**입니다.
검증이 뚫리면 테스트는 조용히 통과하고 인증만 무력해지므로, 실패 경로를 더 많이 둡니다.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from joserfc import jwt
from joserfc.jwe import JWERegistry
from joserfc.jwk import OctKey

from daengs_backend.core.token import (
    ACCESS_TTL,
    AccessClaims,
    TokenExpiredError,
    TokenInvalidError,
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_refresh_token,
)


def _encode_with(key: OctKey, claims: dict) -> str:
    """토큰을 손으로 만듭니다. 우리 발급기를 안 거치는 경우를 만들기 위한 것입니다."""
    return jwt.encode(
        {"alg": "dir", "enc": "A256GCM"}, claims, key, registry=JWERegistry()
    )


class TestAccessToken:
    def test_왕복(self) -> None:
        admin_id = uuid.uuid4()
        claims = decode_access_token(create_access_token(admin_id, "ADMIN"))

        assert isinstance(claims, AccessClaims)
        assert claims.admin_id == admin_id
        assert claims.role == "ADMIN"

    def test_JWE_라서_다섯_조각이다(self) -> None:
        """JWS(서명만)면 세 조각입니다.

        registry 를 빠뜨리면 joserfc 가 조용히 JWS 로 빠지는데, 그때 나가는 토큰은
        base64 디코딩만으로 role 이 읽힙니다. 에러가 안 나므로 조각 수로 잡습니다.
        """
        assert len(create_access_token(uuid.uuid4(), "ADMIN").split(".")) == 5

    def test_내용이_평문으로_보이지_않는다(self) -> None:
        token = create_access_token(uuid.uuid4(), "ADMIN")
        assert "ADMIN" not in token

    def test_수명은_ACCESS_TTL_이다(self) -> None:
        before = datetime.now(UTC)
        claims = decode_access_token(create_access_token(uuid.uuid4(), "VIEWER"))

        # 초 단위로 자르므로 1초 오차를 허용합니다.
        assert abs((claims.expires_at - (before + ACCESS_TTL)).total_seconds()) <= 1

    def test_만료되면_TokenExpiredError(self) -> None:
        past = datetime.now(UTC) - (ACCESS_TTL + timedelta(minutes=1))
        with patch("daengs_backend.core.token.datetime") as clock:
            clock.now.return_value = past
            clock.fromtimestamp = datetime.fromtimestamp
            expired = create_access_token(uuid.uuid4(), "ADMIN")

        with pytest.raises(TokenExpiredError):
            decode_access_token(expired)

    def test_변조되면_TokenInvalidError(self) -> None:
        """GCM 이 위변조를 잡아 주는 것이 이 테스트입니다."""
        token = create_access_token(uuid.uuid4(), "ADMIN")

        with pytest.raises(TokenInvalidError):
            decode_access_token(token[:-4] + "AAAA")

    def test_토큰이_아니면_TokenInvalidError(self) -> None:
        for junk in ["", "not-a-token", "a.b.c", "....."]:
            with pytest.raises(TokenInvalidError):
                decode_access_token(junk)

    def test_다른_키로_만든_토큰은_거부한다(self) -> None:
        other = OctKey.import_key(bytes([9]) * 32)
        forged = _encode_with(
            other,
            {"sub": str(uuid.uuid4()), "role": "ADMIN", "exp": 9999999999},
        )

        with pytest.raises(TokenInvalidError):
            decode_access_token(forged)

    def test_필수_클레임이_빠지면_거부한다(self) -> None:
        """우리 키를 가진 사람만 만들 수 있지만, 그래도 형태를 확인합니다.

        role 이 없는 토큰을 통과시키면 권한 검사가 KeyError 로 터지거나,
        더 나쁘게는 기본값으로 흘러갑니다.
        """
        from daengs_backend.core.token import _KEY

        for claims in [
            {"sub": str(uuid.uuid4()), "exp": 9999999999},  # role 없음
            {"role": "ADMIN", "exp": 9999999999},  # sub 없음
            {"sub": str(uuid.uuid4()), "role": "ADMIN"},  # exp 없음
        ]:
            with pytest.raises(TokenInvalidError):
                decode_access_token(_encode_with(_KEY, claims))

    def test_sub_가_UUID_가_아니면_거부한다(self) -> None:
        from daengs_backend.core.token import _KEY

        bad = _encode_with(
            _KEY, {"sub": "admin", "role": "ADMIN", "exp": 9999999999}
        )
        with pytest.raises(TokenInvalidError):
            decode_access_token(bad)


class TestRefreshToken:
    def test_매번_다른_값이_나온다(self) -> None:
        tokens = {generate_refresh_token() for _ in range(100)}
        assert len(tokens) == 100

    def test_32바이트_urlsafe_base64_다(self) -> None:
        token = generate_refresh_token()

        assert len(token) == 43  # 32바이트를 urlsafe base64 로 하면 43자
        assert "=" not in token  # token_urlsafe 는 패딩을 떼고 줍니다

    def test_해시는_hex_64자다(self) -> None:
        """CHAR(64) 컬럼에 그대로 들어가야 합니다 (03_auth.sql)."""
        digest = hash_refresh_token(generate_refresh_token())

        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)

    def test_같은_원문은_같은_해시다(self) -> None:
        """결정적이어야 DB 에서 행을 찾을 수 있습니다.

        비밀번호 해시(Argon2id)와 정반대입니다 — 그쪽은 매번 달라야 합니다.
        """
        token = generate_refresh_token()
        assert hash_refresh_token(token) == hash_refresh_token(token)

    def test_원문이_해시에_남지_않는다(self) -> None:
        token = generate_refresh_token()
        assert token not in hash_refresh_token(token)
