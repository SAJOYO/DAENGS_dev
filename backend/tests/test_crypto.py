"""core/crypto.py — AES-256-GCM 과 blind index."""

import pytest
from cryptography.exceptions import InvalidTag

from daengs_backend.core.crypto import (
    CURRENT_KEY_VERSION,
    NONCE_SIZE,
    blind_index,
    decrypt,
    encrypt,
)


def test_roundtrip() -> None:
    """암호화 → 복호화가 원문 그대로 나오는가."""
    plaintext = "홍길동@example.com"
    assert decrypt(encrypt(plaintext)) == plaintext


def test_ciphertext_differs_every_time() -> None:
    """nonce 를 매번 새로 뽑는지 봅니다.

    같아진다면 nonce 가 고정된 것이고, GCM 에서는 그 순간 평문이 드러납니다.
    """
    assert encrypt("010-1234-5678") != encrypt("010-1234-5678")


def test_ciphertext_starts_with_key_version() -> None:
    blob = encrypt("x")
    assert blob[0] == CURRENT_KEY_VERSION
    assert len(blob) > 1 + NONCE_SIZE


def test_tampered_ciphertext_fails() -> None:
    """한 바이트만 바꿔도 실패해야 합니다. 이상한 평문이 조용히 나오면 안 됩니다."""
    blob = bytearray(encrypt("변조 금지"))
    blob[-1] ^= 0x01

    with pytest.raises(InvalidTag):
        decrypt(bytes(blob))


def test_unknown_key_version_is_rejected() -> None:
    blob = bytearray(encrypt("x"))
    blob[0] = 99

    with pytest.raises(ValueError, match="키 버전"):
        decrypt(bytes(blob))


def test_too_short_blob_is_rejected() -> None:
    with pytest.raises(ValueError, match="짧"):
        decrypt(b"\x01short")


def test_blind_index_is_deterministic() -> None:
    """같은 입력에는 항상 같은 값. 아니면 조회가 안 됩니다."""
    assert blind_index("a@example.com") == blind_index("a@example.com")


def test_blind_index_normalizes_case_and_space() -> None:
    """정규화가 없으면 같은 사람이 두 번 가입할 수 있습니다."""
    assert blind_index("A@Example.COM") == blind_index("  a@example.com  ")


def test_blind_index_differs_for_different_input() -> None:
    assert blind_index("a@example.com") != blind_index("b@example.com")


def test_blind_index_is_64_hex_chars() -> None:
    """CHAR(64) 컬럼에 그대로 들어가야 합니다."""
    value = blind_index("a@example.com")
    assert len(value) == 64
    assert int(value, 16) >= 0
