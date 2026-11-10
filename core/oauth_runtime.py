"""Trusted host factory for the encrypted OAuth credential vault.

This module deliberately does not initialize keys. The operator must run the
explicit key bootstrap command before constructing the vault.
"""
from __future__ import annotations

from core.oauth_aead import AESGCMCredentialCipher
from core.oauth_credential_vault import SQLiteOAuthCredentialVault
from core.oauth_os_key_provider import OSKeyringOAuthKeyProvider


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
