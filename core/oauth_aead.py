"""AES-256-GCM adapter for the OAuth credential vault.

Key custody is intentionally external: a trusted host key provider supplies the
active encryption key and resolves historical key identifiers for decryption.
"""
from __future__ import annotations

import re
import secrets
from typing import Protocol


class OAuthKeyUnavailable(RuntimeError):
    """A required encryption key is missing or unavailable."""


class OAuthKeyProvider(Protocol):
    """Trusted KMS/OS-keyring adapter; key bytes must never enter model context."""

    def current_key(self) -> tuple[str, bytes]: ...
    def get_key(self, key_id: str) -> bytes: ...


class AESGCMCredentialCipher:
    """Versioned AES-GCM envelope implementing the credential vault cipher contract."""

    _KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}$")
    _NONCE_BYTES = 12
    _TAG_BYTES = 16
    _MAX_PLAINTEXT = 16_384

    def __init__(self, key_provider: OAuthKeyProvider):
        if not callable(getattr(key_provider, "current_key", None)) or not callable(
            getattr(key_provider, "get_key", None)
        ):
            raise TypeError("key_provider must implement current_key and get_key")
        self._key_provider = key_provider

    @classmethod
    def _validate_key(cls, key_id, key):
        if not isinstance(key_id, str) or not cls._KEY_ID.fullmatch(key_id):
            raise OAuthKeyUnavailable("invalid_key_identifier")
        if not isinstance(key, bytes) or len(key) not in (16, 24, 32):
            raise OAuthKeyUnavailable("invalid_aes_key_material")
        return key

    def encrypt(self, plaintext: bytes, *, associated_data: bytes) -> tuple[str, bytes]:
        if not isinstance(plaintext, bytes) or not 1 <= len(plaintext) <= self._MAX_PLAINTEXT:
            raise ValueError("invalid_plaintext_length")
        if not isinstance(associated_data, bytes) or not 1 <= len(associated_data) <= 4096:
            raise ValueError("invalid_associated_data")
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError:
            raise OAuthKeyUnavailable("cryptography_dependency_unavailable") from None
        try:
            key_id, key = self._key_provider.current_key()
            key = self._validate_key(key_id, key)
            nonce = secrets.token_bytes(self._NONCE_BYTES)
            encrypted = AESGCM(key).encrypt(nonce, plaintext, associated_data)
        except OAuthKeyUnavailable:
            raise
        except Exception:
            raise OAuthKeyUnavailable("credential_encryption_failed") from None
        return key_id, nonce + encrypted

    def decrypt(self, key_id: str, ciphertext: bytes, *, associated_data: bytes) -> bytes:
        if not isinstance(key_id, str) or not self._KEY_ID.fullmatch(key_id):
            raise OAuthKeyUnavailable("invalid_key_identifier")
        if not isinstance(ciphertext, bytes) or not self._NONCE_BYTES + self._TAG_BYTES + 1 <= len(ciphertext) <= self._NONCE_BYTES + self._TAG_BYTES + self._MAX_PLAINTEXT:
            raise ValueError("invalid_ciphertext_length")
        if not isinstance(associated_data, bytes) or not 1 <= len(associated_data) <= 4096:
            raise ValueError("invalid_associated_data")
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError:
            raise OAuthKeyUnavailable("cryptography_dependency_unavailable") from None
        try:
            key = self._validate_key(key_id, self._key_provider.get_key(key_id))
            nonce, encrypted = ciphertext[:self._NONCE_BYTES], ciphertext[self._NONCE_BYTES:]
            return AESGCM(key).decrypt(nonce, encrypted, associated_data)
        except OAuthKeyUnavailable:
            raise
        except Exception:
            # Do not expose whether a key identifier exists or authentication failed.
            raise OAuthKeyUnavailable("credential_decryption_failed") from None
