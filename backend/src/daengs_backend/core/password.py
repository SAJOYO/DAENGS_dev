"""관리자 비밀번호 해시 (Argon2id).

**단방향입니다.** 저장한 값에서 비밀번호를 되꺼낼 수 없고, 그게 목적입니다.
원문을 다시 봐야 하는 값(이메일·전화번호 등)은 core/crypto.py 로 갑니다.
비밀번호에 AES 를 쓰면 키를 쥔 사람이 전원의 비밀번호를 읽게 됩니다.

여기 있는 것은 관리자 콘솔 로그인용이고, 앱 회원은 카카오 로그인이라
비밀번호를 받지 않습니다.
"""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

__all__ = [
    "InvalidHashError",
    "VerifyMismatchError",
    "hash_password",
    "needs_rehash",
    "verify_password",
]

# 파라미터는 argon2-cffi 기본값을 그대로 씁니다. 라이브러리가 권장값을 따라
# 갱신해 주므로, 직접 숫자를 박아 두면 오히려 오래된 설정에 고입니다.
#
# salt 는 라이브러리가 매번 새로 뽑아 해시 문자열 안에 넣습니다.
# **salt 컬럼을 따로 만들지 마세요.**
_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """비밀번호를 `$argon2id$v=19$m=...` 형태의 문자열로 만듭니다.

    같은 비밀번호라도 salt 가 달라 매번 다른 값이 나옵니다.
    파라미터와 salt 가 문자열 안에 다 들어 있어 컬럼 하나면 됩니다.
    """
    return _hasher.hash(password)


def verify_password(hashed: str, password: str) -> bool:
    """맞으면 True, **틀리면 VerifyMismatchError 를 냅니다.**

    bool 로 뭉개지 않는 이유는 '틀림'과 '해시가 깨짐'(InvalidHashError)이
    같은 False 가 되면 안 되기 때문입니다. 앞은 401, 뒤는 데이터 사고입니다.

        try:
            verify_password(admin.password_hash, form.password)
        except VerifyMismatchError:
            raise HTTPException(401, "아이디 또는 비밀번호가 올바르지 않습니다")

    401 메시지에 어느 쪽이 틀렸는지 쓰지 마세요. 계정 존재 여부가 새어 나갑니다.
    """
    return _hasher.verify(hashed, password)


def needs_rehash(hashed: str) -> bool:
    """저장된 해시가 지금 파라미터보다 약한지 봅니다.

    라이브러리가 올라가면 기본 파라미터도 올라갑니다. 로그인은 비밀번호 원문을
    아는 유일한 순간이라, verify 가 성공한 직후에만 다시 해시해 저장할 수 있습니다.

        if needs_rehash(admin.password_hash):
            admin.password_hash = hash_password(form.password)
    """
    return _hasher.check_needs_rehash(hashed)
