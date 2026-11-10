import hashlib
import hmac
import tempfile
import unittest
from pathlib import Path

from core.oauth_credential_vault import OAuthCredentialError, SQLiteOAuthCredentialVault


class TestOnlyCipher:
    """Deterministic authenticated test double; never use outside tests."""

    def __init__(self):
        self.key = b"test-only-key-material-for-vault-tests"

    def encrypt(self, plaintext, *, associated_data):
        ciphertext = bytearray()
        for counter, offset in enumerate(range(0, len(plaintext), 32)):
            mask = hmac.new(self.key, associated_data + b"\\0" + counter.to_bytes(4, "big"), hashlib.sha256).digest()
            ciphertext.extend(a ^ b for a, b in zip(plaintext[offset:offset + 32], mask))
        ciphertext = bytes(ciphertext)
        tag = hmac.new(self.key, associated_data + b"\\0" + ciphertext, hashlib.sha256).digest()
        return "test-key-v1", tag + ciphertext

    def decrypt(self, key_id, ciphertext, *, associated_data):
        if key_id != "test-key-v1" or len(ciphertext) < 32:
            raise ValueError("invalid test ciphertext")
        tag, encrypted = ciphertext[:32], ciphertext[32:]
        expected = hmac.new(self.key, associated_data + b"\\0" + encrypted, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected):
            raise ValueError("authentication failed")
        plaintext = bytearray()
        for counter, offset in enumerate(range(0, len(encrypted), 32)):
            mask = hmac.new(self.key, associated_data + b"\\0" + counter.to_bytes(4, "big"), hashlib.sha256).digest()
            plaintext.extend(a ^ b for a, b in zip(encrypted[offset:offset + 32], mask))
        return bytes(plaintext)
