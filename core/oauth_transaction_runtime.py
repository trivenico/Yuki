"""Trusted factory for owner/session-bound OAuth transaction storage."""
from __future__ import annotations

import base64
import json

from core.oauth_aead import AESGCMCredentialCipher
from core.oauth_os_key_provider import OSKeyringOAuthKeyProvider
from core.oauth_transactions import SQLiteOAuthTransactionStore


def _encode_envelope(key_id: str, ciphertext: bytes) -> bytes:
    return json.dumps(
        {"v": 1, "kid": key_id, "ct": base64.b64encode(ciphertext).decode("ascii")},
        separators=(",", ":"), sort_keys=True,
    ).encode("ascii")


def _decode_envelope(value: bytes) -> tuple[str, bytes]:
    if not isinstance(value, bytes) or not 1 <= len(value) <= 24576:
        raise ValueError("invalid_encrypted_transaction")
    try:
        document = json.loads(value.decode("ascii"))
        if not isinstance(document, dict) or set(document) != {"v", "kid", "ct"} or document["v"] != 1:
            raise ValueError
        key_id = document["kid"]
        if not isinstance(key_id, str) or not key_id.startswith("aesgcm-") or len(key_id) != 39:
            raise ValueError
        ciphertext = base64.b64decode(document["ct"], validate=True)
        if not 29 <= len(ciphertext) <= 16413:
            raise ValueError
        return key_id, ciphertext
    except Exception:
        raise ValueError("invalid_encrypted_transaction") from None


def build_oauth_transaction_store(
    database_path: str,
    *,
    key_provider: OSKeyringOAuthKeyProvider | None = None,
) -> SQLiteOAuthTransactionStore:
    """Build a durable transaction store; never initialize OS keys implicitly."""
    provider = key_provider or OSKeyringOAuthKeyProvider()
    cipher = AESGCMCredentialCipher(provider)

    def encrypt(plaintext: bytes, associated_data: bytes) -> bytes:
        key_id, ciphertext = cipher.encrypt(plaintext, associated_data=associated_data)
        return _encode_envelope(key_id, ciphertext)

    def decrypt(envelope: bytes, associated_data: bytes) -> bytes:
        key_id, ciphertext = _decode_envelope(envelope)
        return cipher.decrypt(key_id, ciphertext, associated_data=associated_data)

    return SQLiteOAuthTransactionStore(
        database_path,
        hmac_key=provider.hmac_key(),
        encrypt_with_context=encrypt,
        decrypt_with_context=decrypt,
    )
