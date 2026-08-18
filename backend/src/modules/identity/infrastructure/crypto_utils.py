"""AES-256-GCM encryption utilities for OAuth token storage."""

import ast
import base64
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


@dataclass(frozen=True)
class VersionedSecret:
    format_version: int
    key_version: int
    nonce: str
    ciphertext: str


class CryptoUtils:
    _NONCE_SIZE = 12
    _TAG_SIZE = 16
    _FORMAT_VERSION = 1
    _AAD = b"v1"

    def __init__(
        self,
        encryption_key_b64: str,
        *,
        key_version: int = 1,
        previous_encryption_key_b64: str | None = None,
    ) -> None:
        key = base64.b64decode(encryption_key_b64)
        if len(key) != 32:
            raise ValueError(f"Encryption key must be exactly 32 bytes, got {len(key)} bytes")
        self._aesgcm = AESGCM(key)
        self._key_version = key_version
        self._previous_aesgcm = None
        if previous_encryption_key_b64:
            previous = base64.b64decode(previous_encryption_key_b64)
            if len(previous) != 32:
                raise ValueError(
                    f"Encryption key must be exactly 32 bytes, got {len(previous)} bytes"
                )
            self._previous_aesgcm = AESGCM(previous)

    def encrypt_versioned(self, plaintext: str) -> VersionedSecret:
        nonce = os.urandom(self._NONCE_SIZE)
        encrypted = self._aesgcm.encrypt(nonce, plaintext.encode(), self._AAD)
        return VersionedSecret(
            self._FORMAT_VERSION,
            self._key_version,
            base64.b64encode(nonce).decode(),
            base64.b64encode(encrypted).decode(),
        )

    def encrypt(self, plaintext: str) -> str:
        s = self.encrypt_versioned(plaintext)
        payload = repr(
            {
                "format_version": s.format_version,
                "key_version": s.key_version,
                "nonce": s.nonce,
                "ciphertext": s.ciphertext,
            }
        )
        return base64.b64encode(payload.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        raw = base64.b64decode(ciphertext)
        if raw.startswith(b"{"):
            try:
                p = ast.literal_eval(raw.decode())
                if int(p.get("format_version", 0)) != self._FORMAT_VERSION:
                    raise ValueError("Unsupported ciphertext format version")
                return self._decrypt_with_available_keys(
                    base64.b64decode(p["nonce"]), base64.b64decode(p["ciphertext"])
                )
            except (UnicodeDecodeError, SyntaxError, ValueError, KeyError):
                pass
        if len(raw) < self._NONCE_SIZE + self._TAG_SIZE:
            raise ValueError("Ciphertext too short")
        return self._decrypt_with_available_keys(raw[: self._NONCE_SIZE], raw[self._NONCE_SIZE :])

    def _decrypt_with_available_keys(self, nonce: bytes, encrypted: bytes) -> str:
        last: Exception | None = None
        for key in (self._aesgcm, self._previous_aesgcm):
            if key is None:
                continue
            for aad in (self._AAD, None):
                try:
                    return key.decrypt(nonce, encrypted, aad).decode()
                except Exception as exc:
                    # Intentionally silent per-attempt: this loop tries every
                    # configured key (current, previous) x AAD combination, and
                    # `raise last` below re-raises the final attempt's exception
                    # once all combinations are exhausted. Logging here would fire
                    # on every legacy-format decrypt, since the legacy path always
                    # fails its first (AAD-bound) attempt before succeeding on the
                    # second — that's normal operation, not an error to trace.
                    last = exc
        if last is not None:
            raise last
        raise ValueError("No encryption key configured")

    def redact(self, secret: str | None) -> str | None:
        if secret is None:
            return None
        return "*" * len(secret) if len(secret) <= 4 else "*" * (len(secret) - 4) + secret[-4:]
