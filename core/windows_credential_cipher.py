"""Optional Windows Credential Manager-backed OAuth token cipher.

The native .NET bridge is an explicitly configured, trusted executable. Secrets
are sent over its stdin pipe and returned over stdout; they are never placed in
command-line arguments, environment variables, files, or diagnostic output.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import subprocess

from core.oauth_credential_vault import OAuthCredentialError

_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}$")
_MAX_PLAINTEXT = 8192
_MAX_CIPHERTEXT = 16384
_MAX_AAD = 4096
_MAX_RESPONSE = 32768


class WindowsCredentialCipher:
    """Implements the existing CredentialCipher contract via a .NET helper."""

    def __init__(self, executable_path: str | Path, *, timeout: float = 10.0):
        if os.name != "nt":
            raise RuntimeError("Windows Credential Manager cipher is unavailable on this platform")
        if isinstance(executable_path, str):
            if not executable_path or "\x00" in executable_path:
                raise ValueError("a trusted absolute bridge executable is required")
            path = Path(executable_path)
        elif isinstance(executable_path, Path):
            path = executable_path
        else:
            raise TypeError("executable_path must be a string or Path")
        if not path.is_absolute() or path.is_symlink() or not path.is_file():
            raise ValueError("bridge executable must be an absolute regular non-symlink file")
        if not path.name.lower().endswith(".exe"):
            raise ValueError("bridge executable must be a published Windows executable")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 30:
            raise ValueError("bridge timeout must be in (0, 30]")
        self._executable = str(path)
        self._timeout = float(timeout)

    def encrypt(self, plaintext: bytes, *, associated_data: bytes) -> tuple[str, bytes]:
        if not isinstance(plaintext, bytes) or not 1 <= len(plaintext) <= _MAX_PLAINTEXT:
            raise OAuthCredentialError("invalid_credential_plaintext")
        aad = self._validate_aad(associated_data)
        result = self._invoke({
            "operation": "encrypt",
            "associated_data": base64.b64encode(aad).decode("ascii"),
            "plaintext": base64.b64encode(plaintext).decode("ascii"),
        })
        key_id = result.get("key_id")
        ciphertext_text = result.get("ciphertext")
        if not isinstance(key_id, str) or not _KEY_ID.fullmatch(key_id):
            raise OAuthCredentialError("invalid_encryption_key_id")
        if not isinstance(ciphertext_text, str) or len(ciphertext_text) > 4 * _MAX_CIPHERTEXT:
            raise OAuthCredentialError("invalid_encrypted_credential")
        try:
            ciphertext = base64.b64decode(ciphertext_text, validate=True)
        except (ValueError, TypeError):
            raise OAuthCredentialError("invalid_encrypted_credential") from None
        if not 16 <= len(ciphertext) <= _MAX_CIPHERTEXT:
            raise OAuthCredentialError("invalid_encrypted_credential")
        return key_id, ciphertext

    def decrypt(self, key_id: str, ciphertext: bytes, *, associated_data: bytes) -> bytes:
        if not isinstance(key_id, str) or not _KEY_ID.fullmatch(key_id):
            raise OAuthCredentialError("invalid_encryption_key_id")
        if not isinstance(ciphertext, bytes) or not 29 <= len(ciphertext) <= _MAX_CIPHERTEXT:
            raise OAuthCredentialError("invalid_encrypted_credential")
        aad = self._validate_aad(associated_data)
        result = self._invoke({
            "operation": "decrypt",
            "associated_data": base64.b64encode(aad).decode("ascii"),
            "key_id": key_id,
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        })
        plaintext_text = result.get("plaintext")
        if not isinstance(plaintext_text, str) or len(plaintext_text) > 4 * _MAX_PLAINTEXT:
            raise OAuthCredentialError("credential_decryption_failed")
        try:
            plaintext = base64.b64decode(plaintext_text, validate=True)
        except (ValueError, TypeError):
            raise OAuthCredentialError("credential_decryption_failed") from None
        if not 1 <= len(plaintext) <= _MAX_PLAINTEXT:
            raise OAuthCredentialError("credential_decryption_failed")
        return plaintext

    @staticmethod
    def _validate_aad(value: bytes) -> bytes:
        if not isinstance(value, bytes) or len(value) > _MAX_AAD:
            raise OAuthCredentialError("invalid_credential_associated_data")
        return value

    def _invoke(self, request: dict) -> dict:
        try:
            payload = json.dumps(request, separators=(",", ":"), ensure_ascii=True)
            if len(payload.encode("utf-8")) > 32 * 1024:
                raise OAuthCredentialError("credential_request_too_large")
            completed = subprocess.run(
                [self._executable],
                input=payload,
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=self._timeout,
                check=False,
                shell=False,
                creationflags=0x08000000,
                env={},
            )
        except subprocess.TimeoutExpired:
            raise OAuthCredentialError("credential_operation_timeout") from None
        except (OSError, UnicodeError, ValueError):
            raise OAuthCredentialError("credential_bridge_unavailable") from None
        output = completed.stdout
        if completed.returncode != 0 or not isinstance(output, str) or len(output) > _MAX_RESPONSE:
            raise OAuthCredentialError("credential_operation_failed")
        try:
            result = json.loads(output)
        except (TypeError, ValueError):
            raise OAuthCredentialError("credential_operation_failed") from None
        if not isinstance(result, dict) or "error" in result:
            raise OAuthCredentialError("credential_operation_failed")
        return result
