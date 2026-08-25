"""개인정보 암복호화 (AES-256-GCM) 와 blind index (HMAC-SHA256).

여기 있는 것은 **나중에 원문을 다시 꺼내 봐야 하는** 값을 위한 것입니다.
비밀번호처럼 복호화되면 안 되는 값은 core/password.py 로 갑니다.

    이메일        encrypt() + blind_index()   원문 필요 + 같은 값 조회 필요
    전화번호·이름  encrypt()                   원문 필요
    kakao_id      평문                        암호화하지 않습니다

DB 를 모릅니다. str 을 받아 bytes 를 돌려주는 순수 함수뿐입니다.
"""

import base64
import hashlib
import hmac
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from daengs_backend.config import settings

KEY_SIZE = 32  # AES-256
NONCE_SIZE = 12  # GCM 권장 길이(96비트)

# 암호문 맨 앞에 붙는 키 버전. 지금은 1 하나뿐입니다.
#
# 키를 갈 때는 새 키를 2로 _KEYS 에 추가하고 이 값을 2로 올립니다.
# **1은 지우지 마세요** — 아직 재암호화되지 않은 옛 암호문이 그 키로만 열립니다.
# 버전 바이트가 없으면 전수 재암호화 도중에 어느 키로 암호화된 행인지
# 알 방법이 없습니다. 지금 넣으면 1바이트지만 나중에 넣으려면 못 넣습니다.
CURRENT_KEY_VERSION = 1


def _load_key(env_name: str, encoded: str) -> bytes:
    """base64 문자열을 32바이트 키로 풉니다.

    앱이 뜰 때 한 번 돌고, 잘못된 키면 여기서 바로 죽습니다.
    첫 암복호화 요청까지 미뤄 두면 문제를 훨씬 늦게 발견합니다.
    """
    try:
        key = base64.urlsafe_b64decode(encoded)
    except ValueError as exc:  # binascii.Error 가 ValueError 입니다
        raise ValueError(f"{env_name} 가 base64 가 아닙니다.") from exc

    if len(key) != KEY_SIZE:
        # 값 자체는 절대 메시지에 넣지 마세요. 로그로 새어 나갑니다.
        raise ValueError(
            f"{env_name} 는 {KEY_SIZE}바이트여야 합니다 (지금 {len(key)}바이트). "
            "secrets.token_bytes(32) 를 urlsafe base64 로 인코딩한 값을 넣으세요."
        )
    return key


# 버전 → 키. 복호화는 암호문 첫 바이트를 보고 여기서 키를 고릅니다.
_KEYS: dict[int, bytes] = {
    1: _load_key("DAENGS_AES_KEY", settings.aes_key.get_secret_value()),
}

# AES 키와 반드시 다른 키입니다. 같은 값을 쓰면 나눠 둔 의미가 없습니다.
_BLIND_INDEX_KEY = _load_key(
    "DAENGS_BLIND_INDEX_KEY", settings.blind_index_key.get_secret_value()
)


def encrypt(plaintext: str) -> bytes:
    """평문을 `[버전 1B][nonce 12B][암호문+태그]` 로 돌려줍니다. 컬럼은 BYTEA.

    같은 평문을 두 번 넣어도 결과가 다릅니다 (nonce 가 매번 바뀌므로).
    그래서 **암호문끼리 비교해서 같은 값을 찾을 수 없습니다** — 조회가 필요하면
    blind_index() 를 같이 저장하세요.
    """
    # nonce 는 매번 새로 뽑습니다. 같은 키에 같은 nonce 를 두 번 쓰면
    # GCM 은 평문이 복원됩니다. 절대 고정하거나 카운터로 만들지 마세요.
    nonce = os.urandom(NONCE_SIZE)
    ciphertext = AESGCM(_KEYS[CURRENT_KEY_VERSION]).encrypt(
        nonce, plaintext.encode(), None
    )
    # nonce 는 비밀이 아닙니다. 복호화에 필요하므로 암호문에 붙여 같이 저장합니다.
    return bytes([CURRENT_KEY_VERSION]) + nonce + ciphertext


def decrypt(blob: bytes) -> str:
    """encrypt() 가 만든 바이트열을 원문으로 되돌립니다.

    변조되었거나 키가 다르면 `cryptography.exceptions.InvalidTag` 가 올라옵니다.
    **삼키지 마세요.** GCM 이 위변조를 잡아 준다는 것이 이 예외이고,
    조용히 넘기면 잡아 준 것이 없는 것과 같습니다.
    """
    if len(blob) <= 1 + NONCE_SIZE:
        raise ValueError("암호문이 너무 짧습니다. encrypt() 가 만든 값이 아닙니다.")

    version = blob[0]
    key = _KEYS.get(version)
    if key is None:
        # 키를 갈면서 _KEYS 에서 옛 버전을 지웠을 때 여기로 옵니다.
        raise ValueError(f"모르는 키 버전입니다: {version}")

    nonce = blob[1 : 1 + NONCE_SIZE]
    return AESGCM(key).decrypt(nonce, blob[1 + NONCE_SIZE :], None).decode()


def blind_index(value: str) -> str:
    """같은 값을 찾기 위한 HMAC-SHA256 hex 64자. 컬럼은 CHAR(64).

    암호문은 매번 달라서 `WHERE email = ?` 가 안 되므로, 이 값을 따로 저장해
    조회와 UNIQUE 제약에 씁니다.

    단순 SHA-256 이 아니라 HMAC 인 이유는, 키가 없으면 사전공격이 안 되게 하기
    위해서입니다. 이메일 주소는 후보가 뻔해서 그냥 해시하면 다 맞힙니다.
    """
    # 정규화하고 해시합니다. 안 하면 'A@x.com' 과 'a@x.com' 이 다른 값이 되어
    # 중복가입을 못 막습니다. 지금 쓰는 대상이 이메일이라 소문자로 통일합니다.
    normalized = value.strip().lower()
    return hmac.new(
        _BLIND_INDEX_KEY, normalized.encode(), hashlib.sha256
    ).hexdigest()
