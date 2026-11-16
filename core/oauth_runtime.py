"""Trusted host factories for durable, encrypted OAuth runtime storage.

These factories deliberately do not initialize keys. The operator must run the
explicit key bootstrap command before constructing either store.
"""
from __future__ import annotations

import re

from core.oauth_aead import AESGCMCredentialCipher
from core.oauth_credential_vault import SQLiteOAuthCredentialVault
from core.oauth_os_key_provider import OSKeyringOAuthKeyProvider
from core.oauth_lifecycle_coordinator import SQLiteOAuthLifecycleCoordinator
from core.oauth_transactions import SQLiteOAuthTransactionStore


_TRANSACTION_AAD = b"yuki-oauth-transaction-v1"
_TRANSACTION_ENVELOPE = b"YUKIOT1|"
_KEY_ID = re.compile(rb"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}$")


def _transaction_encrypt(cipher, plaintext: bytes) -> bytes:
    key_id, ciphertext = cipher.encrypt(plaintext, associated_data=_TRANSACTION_AAD)
    if not isinstance(key_id, str):
        raise ValueError("invalid_transaction_key_id")
    try:
        encoded_key_id = key_id.encode("ascii")
    except UnicodeEncodeError:
        raise ValueError("invalid_transaction_key_id") from None
    if not _KEY_ID.fullmatch(encoded_key_id):
        raise ValueError("invalid_transaction_key_id")
    if not isinstance(ciphertext, bytes) or not ciphertext:
        raise ValueError("invalid_transaction_ciphertext")
    return _TRANSACTION_ENVELOPE + encoded_key_id + b"|" + ciphertext


def _transaction_decrypt(cipher, envelope: bytes) -> bytes:
    if not isinstance(envelope, bytes) or not envelope.startswith(_TRANSACTION_ENVELOPE):
        raise ValueError("invalid_transaction_ciphertext")
    key_id, separator, ciphertext = envelope[len(_TRANSACTION_ENVELOPE):].partition(b"|")
    if not separator or not _KEY_ID.fullmatch(key_id) or not ciphertext:
        raise ValueError("invalid_transaction_ciphertext")
    return cipher.decrypt(
        key_id.decode("ascii"), ciphertext, associated_data=_TRANSACTION_AAD,
    )


def build_oauth_credential_vault(
    database_path: str,
    *,
    key_provider: OSKeyringOAuthKeyProvider | None = None,
) -> SQLiteOAuthCredentialVault:
    """Build the vault from the native OS key store; never bootstrap implicitly."""
    provider = key_provider or OSKeyringOAuthKeyProvider()
    cipher = AESGCMCredentialCipher(provider)
    # hmac_key() fails closed until explicit OS-key-store initialization.
    return SQLiteOAuthCredentialVault(
        database_path,
        cipher=cipher,
        hmac_key=provider.hmac_key(),
    )


def build_oauth_transaction_store(
    database_path: str,
    *,
    key_provider: OSKeyringOAuthKeyProvider | None = None,
) -> SQLiteOAuthTransactionStore:
    """Build durable PKCE/nonce storage with OS-keyring AES-GCM and HMAC keys.

    The transaction store accepts byte callbacks rather than a versioned cipher
    interface. This adapter records the non-secret key identifier beside the
    authenticated ciphertext so historical keys remain usable after rotation.
    No key material is written to the transaction database.
    """
    provider = key_provider or OSKeyringOAuthKeyProvider()
    cipher = AESGCMCredentialCipher(provider)
    hmac_key = provider.hmac_key()  # Fails closed until explicit operator bootstrap.
    return SQLiteOAuthTransactionStore(
        database_path,
        hmac_key=hmac_key,
        encrypt=lambda value: _transaction_encrypt(cipher, value),
        decrypt=lambda value: _transaction_decrypt(cipher, value),
    )


def build_oauth_lifecycle_coordinator(
    database_path: str, *, busy_timeout_ms: int = 5000,
) -> SQLiteOAuthLifecycleCoordinator:
    """Build the shared process-safe lock used by all OAuth lifecycle services.

    Every API/worker process that shares a credential vault must receive a
    coordinator configured with the same local-filesystem database path.
    """
    return SQLiteOAuthLifecycleCoordinator(
        database_path, busy_timeout_ms=busy_timeout_ms,
    )
