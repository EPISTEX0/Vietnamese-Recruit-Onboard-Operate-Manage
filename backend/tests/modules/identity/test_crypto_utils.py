"""Unit tests for CryptoUtils AES-256-GCM encryption."""

import base64
import os

import pytest
from cryptography.exceptions import InvalidTag

from src.modules.identity.container import get_crypto_utils
from src.modules.identity.infrastructure.crypto_utils import CryptoUtils
from tests.container_cache_support import reset_cached_app_containers


def _generate_valid_key_b64() -> str:
    """Generate a valid base64-encoded 32-byte key for testing."""
    return base64.b64encode(os.urandom(32)).decode("ascii")


@pytest.fixture
def crypto() -> CryptoUtils:
    """Create a CryptoUtils instance with a random valid key."""
    return CryptoUtils(_generate_valid_key_b64())


class TestCryptoUtilsInit:
    """Tests for CryptoUtils initialization."""

    def test_accepts_valid_32_byte_key(self) -> None:
        key = _generate_valid_key_b64()
        utils = CryptoUtils(key)
        assert utils is not None

    def test_rejects_key_shorter_than_32_bytes(self) -> None:
        short_key = base64.b64encode(os.urandom(16)).decode("ascii")
        with pytest.raises(ValueError, match="must be exactly 32 bytes"):
            CryptoUtils(short_key)

    def test_rejects_key_longer_than_32_bytes(self) -> None:
        long_key = base64.b64encode(os.urandom(64)).decode("ascii")
        with pytest.raises(ValueError, match="must be exactly 32 bytes"):
            CryptoUtils(long_key)


class TestEncryptDecryptRoundTrip:
    """Tests for encrypt/decrypt round-trip correctness."""

    def test_round_trip_simple_string(self, crypto: CryptoUtils) -> None:
        plaintext = "hello world"
        encrypted = crypto.encrypt(plaintext)
        decrypted = crypto.decrypt(encrypted)
        assert decrypted == plaintext

    def test_round_trip_empty_string(self, crypto: CryptoUtils) -> None:
        plaintext = ""
        encrypted = crypto.encrypt(plaintext)
        decrypted = crypto.decrypt(encrypted)
        assert decrypted == plaintext

    def test_round_trip_unicode(self, crypto: CryptoUtils) -> None:
        plaintext = "Xin chào thế giới 🌍"
        encrypted = crypto.encrypt(plaintext)
        decrypted = crypto.decrypt(encrypted)
        assert decrypted == plaintext

    def test_round_trip_long_string(self, crypto: CryptoUtils) -> None:
        plaintext = "a" * 10_000
        encrypted = crypto.encrypt(plaintext)
        decrypted = crypto.decrypt(encrypted)
        assert decrypted == plaintext

    def test_round_trip_json_token(self, crypto: CryptoUtils) -> None:
        plaintext = '{"access_token":"ya29.abc123","refresh_token":"1//xyz789"}'
        encrypted = crypto.encrypt(plaintext)
        decrypted = crypto.decrypt(encrypted)
        assert decrypted == plaintext


class TestEncryptionProperties:
    """Tests for encryption security properties."""

    def test_encrypted_differs_from_plaintext(self, crypto: CryptoUtils) -> None:
        plaintext = "sensitive-token-value"
        encrypted = crypto.encrypt(plaintext)
        assert encrypted != plaintext

    def test_different_nonces_produce_different_ciphertexts(self, crypto: CryptoUtils) -> None:
        plaintext = "same-input-different-output"
        encrypted1 = crypto.encrypt(plaintext)
        encrypted2 = crypto.encrypt(plaintext)
        # Two encryptions of the same plaintext should differ (random nonce)
        assert encrypted1 != encrypted2

    def test_output_is_valid_base64(self, crypto: CryptoUtils) -> None:
        encrypted = crypto.encrypt("test")
        # Should not raise
        decoded = base64.b64decode(encrypted)
        # nonce (12) + tag (16) + at least 0 bytes ciphertext
        assert len(decoded) >= 28


class TestDecryptionFailures:
    """Tests for decryption error handling."""

    def test_wrong_key_fails_to_decrypt(self) -> None:
        key1 = _generate_valid_key_b64()
        key2 = _generate_valid_key_b64()
        crypto1 = CryptoUtils(key1)
        crypto2 = CryptoUtils(key2)

        encrypted = crypto1.encrypt("secret data")
        with pytest.raises(InvalidTag):
            crypto2.decrypt(encrypted)

    def test_tampered_ciphertext_fails(self, crypto: CryptoUtils) -> None:
        encrypted = crypto.encrypt("important data")
        # Decode, flip a byte in the ciphertext portion, re-encode
        raw = bytearray(base64.b64decode(encrypted))
        # Flip a byte after the nonce (in ciphertext area)
        if len(raw) > 12:
            raw[12] ^= 0xFF
        tampered = base64.b64encode(bytes(raw)).decode("ascii")
        with pytest.raises(InvalidTag):
            crypto.decrypt(tampered)

    def test_truncated_input_fails(self, crypto: CryptoUtils) -> None:
        # Input too short to contain nonce + tag
        short_data = base64.b64encode(os.urandom(10)).decode("ascii")
        with pytest.raises(ValueError, match="Ciphertext too short"):
            crypto.decrypt(short_data)

    def test_invalid_base64_fails(self, crypto: CryptoUtils) -> None:
        with pytest.raises(Exception):
            crypto.decrypt("not-valid-base64!!!")


class TestVersionedEnvelope:
    def test_encrypt_returns_versioned_envelope(self, crypto: CryptoUtils) -> None:
        encrypted = crypto.encrypt("secret")
        payload = base64.b64decode(encrypted).decode("utf-8")
        assert "format_version" in payload
        assert "key_version" in payload
        assert "nonce" in payload
        assert "ciphertext" in payload

    def test_decrypt_legacy_nonce_ciphertext_format(self) -> None:
        key = _generate_valid_key_b64()
        crypto = CryptoUtils(key)
        nonce = os.urandom(12)
        legacy_ciphertext = crypto._aesgcm.encrypt(nonce, b"legacy", b"v1")
        raw = base64.b64encode(nonce + legacy_ciphertext).decode("ascii")
        assert crypto.decrypt(raw) == "legacy"

    def test_previous_key_can_decrypt_legacy_payload(self) -> None:
        current_key = _generate_valid_key_b64()
        previous_key = _generate_valid_key_b64()
        writer = CryptoUtils(previous_key)
        nonce = os.urandom(12)
        legacy_ciphertext = writer._aesgcm.encrypt(nonce, b"old", b"v1")
        raw = base64.b64encode(nonce + legacy_ciphertext).decode("ascii")
        reader = CryptoUtils(current_key, previous_encryption_key_b64=previous_key)
        assert reader.decrypt(raw) == "old"


class TestGetCryptoUtilsContainer:
    """``get_crypto_utils()`` must build for real off the suite's pinned env.

    Every other test that reaches this factory monkeypatches around it,
    because ``tests/env_isolation.py`` used to pin a key that decoded to 28
    bytes and the real call raised ``ValueError`` (#333). This is the one test
    that calls the ``@lru_cache``d factory bare -- no patch, no stub -- so a
    regression to a short pinned key is caught here instead of resurfacing as
    a wall of unrelated ``ValueError`` failures the next time someone adds a
    test that reaches ``CryptoUtils`` through the container.
    """

    def test_builds_a_working_singleton_from_pinned_env(self) -> None:
        reset_cached_app_containers()
        try:
            crypto = get_crypto_utils()
            encrypted = crypto.encrypt("round-trip-through-the-real-container")
            assert crypto.decrypt(encrypted) == "round-trip-through-the-real-container"
        finally:
            reset_cached_app_containers()
