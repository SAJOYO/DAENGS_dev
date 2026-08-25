"""core/password.py — Argon2id."""

import pytest

from daengs_backend.core.password import (
    InvalidHashError,
    VerifyMismatchError,
    hash_password,
    needs_rehash,
    verify_password,
)

PASSWORD = "관리자비밀번호1234!"


def test_hash_differs_every_time() -> None:
    """salt 가 매번 새로 붙는지. 같아진다면 같은 비밀번호를 쓰는 계정이 드러납니다."""
    assert hash_password(PASSWORD) != hash_password(PASSWORD)


def test_both_hashes_verify() -> None:
    """해시가 서로 달라도 둘 다 통과해야 합니다 (salt 가 해시 안에 들어 있으므로)."""
    for hashed in (hash_password(PASSWORD), hash_password(PASSWORD)):
        assert verify_password(hashed, PASSWORD) is True


def test_hash_does_not_contain_plaintext() -> None:
    assert PASSWORD not in hash_password(PASSWORD)


def test_hash_is_argon2id() -> None:
    """argon2i / argon2d 가 아니라 argon2id 여야 합니다."""
    assert hash_password(PASSWORD).startswith("$argon2id$")


def test_wrong_password_raises() -> None:
    with pytest.raises(VerifyMismatchError):
        verify_password(hash_password(PASSWORD), "틀린비밀번호")


def test_broken_hash_raises_different_error() -> None:
    """'비밀번호 틀림'(401)과 '데이터가 깨짐'을 구분할 수 있어야 합니다."""
    with pytest.raises(InvalidHashError):
        verify_password("not-a-hash", PASSWORD)


def test_fresh_hash_needs_no_rehash() -> None:
    assert needs_rehash(hash_password(PASSWORD)) is False


def test_weak_hash_needs_rehash() -> None:
    """라이브러리 기본 파라미터가 올라갔을 때를 흉내 냅니다."""
    from argon2 import PasswordHasher

    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(PASSWORD)
    assert needs_rehash(weak) is True
