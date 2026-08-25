"""환경 변수로 받은 base64 키를 원시 바이트로 푸는 절차.

crypto.py(개인정보 AES) 와 token.py(access token JWE) 가 같은 형식의 키를 받아서
검증 절차만 여기 모았습니다. **키의 용도가 합쳐진 것은 아닙니다** (D-012) —
어떤 키를 어디에 쓰는지는 여전히 각 모듈이 따로 정하고, 셋을 섞어 쓰면 안 됩니다.

여기 있는 것은 "urlsafe base64 32바이트인지 확인하고 푼다" 하나뿐입니다.
"""

import base64

KEY_SIZE = 32  # AES-256 / A256GCM 둘 다 32바이트입니다

__all__ = ["KEY_SIZE", "load_key"]


def load_key(env_name: str, encoded: str) -> bytes:
    """base64 문자열을 32바이트 키로 풉니다.

    앱이 뜰 때 한 번 돌고, 잘못된 키면 여기서 바로 죽습니다.
    첫 암복호화(또는 첫 로그인)까지 미뤄 두면 문제를 훨씬 늦게 발견합니다.

    **값 자체는 절대 예외 메시지에 넣지 마세요.** 부팅 실패 로그로 그대로 새어 나갑니다.
    config.py 가 ValidationError 에서 값을 걷어내는 것과 같은 이유입니다.
    """
    try:
        key = base64.urlsafe_b64decode(encoded)
    except ValueError as exc:  # binascii.Error 가 ValueError 입니다
        raise ValueError(f"{env_name} 가 base64 가 아닙니다.") from exc

    if len(key) != KEY_SIZE:
        raise ValueError(
            f"{env_name} 는 {KEY_SIZE}바이트여야 합니다 (지금 {len(key)}바이트). "
            "secrets.token_bytes(32) 를 urlsafe base64 로 인코딩한 값을 넣으세요."
        )
    return key
